import asyncio
from uuid import UUID

import httpx
import pytest

from payments.domain.errors import AcquirerUnavailableError
from payments.domain.payment import PaymentStatus
from tests.fakes import NEXT_YEAR, VALID_PAYMENT

IDEMPOTENCY_HEADER = {"Idempotency-Key": "order-42-attempt-1"}
BANK_NOT_REACHED = AcquirerUnavailableError("not_sent", safe_to_retry=True)
BANK_ANSWER_LOST = AcquirerUnavailableError("timeout")


def test_authorized_payment_returns_201_with_masked_card(make_client):
    client, bank, payments = make_client(PaymentStatus.AUTHORIZED)

    response = client.post("/v1/payments", json=VALID_PAYMENT)

    body = response.json()
    assert response.status_code == 201
    assert body == {
        "id": body["id"],
        "status": "Authorized",
        "last_four": "8877",
        "expiry_month": 4,
        "expiry_year": NEXT_YEAR,
        "currency": "GBP",
        "amount": 100,
    }
    assert VALID_PAYMENT["card_number"] not in response.text
    assert payments.get(UUID(body["id"])) is not None
    assert len(bank.calls) == 1
    assert response.headers["Location"] == f"/v1/payments/{body['id']}"


def test_declined_payment_returns_201(make_client):
    client, _, _ = make_client(PaymentStatus.DECLINED)

    response = client.post("/v1/payments", json=VALID_PAYMENT)

    assert response.status_code == 201
    assert response.json()["status"] == "Declined"


def test_lost_bank_answer_returns_202_pending_and_the_payment_can_be_retrieved(make_client):
    client, _, _ = make_client(BANK_ANSWER_LOST)

    response = client.post("/v1/payments", json=VALID_PAYMENT)
    fetched = client.get(response.headers["Location"])

    assert response.status_code == 202
    assert response.json()["status"] == "Pending"
    assert fetched.status_code == 200
    assert fetched.json() == response.json()


def test_bank_not_reached_returns_502_and_stores_nothing(make_client):
    client, _, payments = make_client(BANK_NOT_REACHED)

    response = client.post("/v1/payments", json=VALID_PAYMENT)

    assert response.status_code == 502
    assert response.json() == {"error": "Acquiring bank unavailable"}
    assert payments.count() == 0


def test_payment_the_bank_never_received_is_not_retrievable(make_client):
    client, _, payments = make_client(BANK_NOT_REACHED)

    response = client.post("/v1/payments", json=VALID_PAYMENT)

    assert response.status_code == 502
    assert "Location" not in response.headers
    assert payments.count() == 0


@pytest.mark.parametrize(
    "invalid_fields",
    [
        {"card_number": "2222abcd43248877"},
        {"expiry_month": 13},
        {"amount": "100"},
        {"amount": 100.0},
        {"currency": "gbp"},
    ],
)
def test_invalid_request_is_rejected_with_400_without_calling_bank(make_client, invalid_fields):
    client, bank, payments = make_client()

    response = client.post("/v1/payments", json=VALID_PAYMENT | invalid_fields)

    body = response.json()
    assert response.status_code == 400
    assert body["status"] == "Rejected"
    assert [error["field"] for error in body["errors"]] == list(invalid_fields)
    assert bank.calls == []
    assert payments.count() == 0


def test_missing_field_is_reported(make_client):
    client, _, _ = make_client()
    payload_without_cvv = {field: value for field, value in VALID_PAYMENT.items() if field != "cvv"}

    response = client.post("/v1/payments", json=payload_without_cvv)

    assert response.status_code == 400
    assert response.json()["errors"] == [{"field": "cvv", "message": "Field required"}]


def test_all_invalid_fields_are_reported_together(make_client):
    client, _, _ = make_client()

    response = client.post("/v1/payments", json=VALID_PAYMENT | {"cvv": "1", "currency": "JPY"})

    assert {error["field"] for error in response.json()["errors"]} == {"cvv", "currency"}


def test_malformed_json_is_rejected(make_client):
    client, bank, _ = make_client()

    response = client.post("/v1/payments", content="{not json", headers={"Content-Type": "application/json"})

    assert response.status_code == 400
    assert response.json()["status"] == "Rejected"
    assert bank.calls == []


def test_rejection_does_not_echo_card_number(make_client):
    client, _, _ = make_client()

    response = client.post("/v1/payments", json=VALID_PAYMENT | {"cvv": "1"})

    assert VALID_PAYMENT["card_number"] not in response.text


def test_retry_with_same_key_replays_the_payment_without_calling_bank(make_client):
    client, bank, _ = make_client()

    first = client.post("/v1/payments", json=VALID_PAYMENT, headers=IDEMPOTENCY_HEADER)
    retry = client.post("/v1/payments", json=VALID_PAYMENT, headers=IDEMPOTENCY_HEADER)

    assert first.status_code == retry.status_code == 201
    assert retry.json() == first.json()
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert "Idempotent-Replayed" not in first.headers
    assert retry.headers["Location"] == first.headers["Location"]
    assert len(bank.calls) == 1


def test_same_key_with_different_body_returns_409_without_calling_bank(make_client):
    client, bank, _ = make_client()
    client.post("/v1/payments", json=VALID_PAYMENT, headers=IDEMPOTENCY_HEADER)

    response = client.post("/v1/payments", json=VALID_PAYMENT | {"amount": 999}, headers=IDEMPOTENCY_HEADER)

    assert response.status_code == 409
    assert len(bank.calls) == 1
    assert response.json() == {"error": "Idempotency-Key reused with a different request"}


def test_retry_after_a_lost_bank_answer_replays_the_pending_payment_without_calling_bank(make_client):
    client, bank, _ = make_client(BANK_ANSWER_LOST)
    first = client.post("/v1/payments", json=VALID_PAYMENT, headers=IDEMPOTENCY_HEADER)
    bank.result = PaymentStatus.AUTHORIZED

    retry = client.post("/v1/payments", json=VALID_PAYMENT, headers=IDEMPOTENCY_HEADER)

    assert retry.status_code == 202
    assert retry.json() == first.json()
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert len(bank.calls) == 1


def test_retry_after_rejection_is_allowed_with_same_key(make_client):
    client, bank, _ = make_client()
    rejected = client.post("/v1/payments", json=VALID_PAYMENT | {"cvv": "1"}, headers=IDEMPOTENCY_HEADER)

    fixed = client.post("/v1/payments", json=VALID_PAYMENT, headers=IDEMPOTENCY_HEADER)

    assert rejected.status_code == 400
    assert fixed.status_code == 201
    assert len(bank.calls) == 1


@pytest.mark.parametrize(
    "invalid_key",
    ["", "x" * 256, "has space", "clé-1".encode()],
    ids=["empty", "too-long", "space", "non-ascii"],
)
def test_invalid_idempotency_key_is_rejected(make_client, invalid_key):
    client, bank, _ = make_client()

    response = client.post("/v1/payments", json=VALID_PAYMENT, headers={"Idempotency-Key": invalid_key})

    assert response.status_code == 400
    assert response.json()["errors"][0]["field"] == "idempotency-key"
    assert bank.calls == []


async def test_concurrent_duplicate_replays_the_pending_payment_while_first_is_at_bank(make_client):
    test_client, bank, payments = make_client()
    first_reached_bank, release_bank = asyncio.Event(), asyncio.Event()
    authorize = bank.authorize

    async def authorize_when_released(request, payment_id):
        first_reached_bank.set()
        await release_bank.wait()
        return await authorize(request, payment_id)

    bank.authorize = authorize_when_released
    transport = httpx.ASGITransport(app=test_client.app)

    async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
        first_request = asyncio.create_task(client.post("/v1/payments", json=VALID_PAYMENT, headers=IDEMPOTENCY_HEADER))
        await first_reached_bank.wait()

        duplicate = await client.post("/v1/payments", json=VALID_PAYMENT, headers=IDEMPOTENCY_HEADER)
        release_bank.set()
        first = await first_request

    assert duplicate.status_code == 202
    assert duplicate.json()["status"] == "Pending"
    assert duplicate.json()["id"] == first.json()["id"]
    assert first.status_code == 201
    assert len(bank.calls) == 1
    assert payments.count() == 1


@pytest.mark.parametrize("status", [PaymentStatus.AUTHORIZED, PaymentStatus.DECLINED])
def test_created_payment_can_be_retrieved(make_client, status):
    client, _, _ = make_client(status)
    created = client.post("/v1/payments", json=VALID_PAYMENT)

    response = client.get(created.headers["Location"])

    assert response.status_code == 200
    assert response.json() == created.json()
    assert response.headers["Cache-Control"] == "no-store"
    assert VALID_PAYMENT["card_number"] not in response.text


@pytest.mark.parametrize(
    "payment_id",
    ["9a09ef5f-0707-474f-b9a8-f7507bd99470", "not-a-uuid", "123"],
    ids=["unknown-uuid", "malformed", "number"],
)
def test_unknown_or_malformed_id_returns_404(make_client, payment_id):
    client, _, _ = make_client()

    response = client.get(f"/v1/payments/{payment_id}")

    assert response.status_code == 404
    assert response.json() == {"error": "Payment not found"}
