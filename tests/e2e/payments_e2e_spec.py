"""End-to-end: real app, real wiring, real bank simulator. Skipped if the simulator is down.

The simulator decides by the card's last digit: odd -> authorized, even -> declined, 0 -> 503.
"""

import socket
from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests.fakes import VALID_PAYMENT

AUTHORIZED_CARD = "2222405343248877"
DECLINED_CARD = "2222405343248878"
BANK_ERROR_CARD = "2222405343248870"


def _simulator_running() -> bool:
    try:
        socket.create_connection(("localhost", 8080), timeout=0.5).close()
        return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not _simulator_running(), reason="bank simulator not running (docker-compose up)"),
]


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    with TestClient(app) as client:  # `with` runs lifespan, wiring the real adapters
        yield client


def pay(client: TestClient, card_number: str = AUTHORIZED_CARD, key: str | None = None, **overrides):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post("/v1/payments", json={**VALID_PAYMENT, "card_number": card_number, **overrides}, headers=headers)


@pytest.mark.parametrize(
    "card_number, status",
    [(AUTHORIZED_CARD, "Authorized"), (DECLINED_CARD, "Declined")],
)
def test_bank_decision_is_stored_and_retrievable(client, card_number, status):
    created = pay(client, card_number)
    fetched = client.get(created.headers["Location"])

    assert created.status_code == 201
    assert created.json()["status"] == status
    assert fetched.status_code == 200
    assert fetched.json() == created.json()


def test_response_never_contains_the_full_card_number_or_cvv(client):
    created = pay(client)
    fetched = client.get(created.headers["Location"])

    for response in (created, fetched):
        assert response.json()["last_four"] == "8877"
        assert AUTHORIZED_CARD not in response.text
        assert '"cvv"' not in response.text


def test_bank_error_returns_502_and_stores_nothing(client):
    response = pay(client, BANK_ERROR_CARD)

    assert response.status_code == 502
    assert response.json() == {"error": "Acquiring bank unavailable"}
    assert "Location" not in response.headers


def test_invalid_payment_is_rejected_before_reaching_the_bank(client):
    response = pay(client, card_number="1234", currency="JPY", amount=0)

    assert response.status_code == 400
    assert response.json()["status"] == "Rejected"
    assert {error["field"] for error in response.json()["errors"]} == {"card_number", "currency", "amount"}


def test_retry_with_same_key_replays_the_original_payment(client):
    key = f"e2e-{uuid4()}"

    first = pay(client, key=key)
    retry = pay(client, key=key)

    assert retry.status_code == 201
    assert retry.json() == first.json()
    assert retry.headers["Idempotent-Replayed"] == "true"


def test_same_key_with_a_different_payment_is_refused(client):
    key = f"e2e-{uuid4()}"

    pay(client, key=key)
    other = pay(client, key=key, amount=999)

    assert other.status_code == 409


def test_retry_after_bank_503_is_sent_to_the_bank_again(client):
    # A 503 means the bank did not charge, so the key is freed: the retry reaches the bank (503 again)
    # instead of being blocked with the "may have been charged" error.
    key = f"e2e-{uuid4()}"

    first = pay(client, BANK_ERROR_CARD, key=key)
    retry = pay(client, BANK_ERROR_CARD, key=key)

    assert first.status_code == 502
    assert retry.status_code == 502
    assert retry.json() == {"error": "Acquiring bank unavailable"}


@pytest.mark.parametrize("payment_id", [str(uuid4()), "not-a-uuid"])
def test_unknown_payment_returns_404(client, payment_id):
    response = client.get(f"/v1/payments/{payment_id}")

    assert response.status_code == 404
