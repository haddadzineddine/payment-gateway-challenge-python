import json
from uuid import uuid4

import httpx
import pytest

from payments.adapters.bank.mountebank import MountebankClient
from payments.domain.errors import AcquirerUnavailableError
from payments.domain.payment import PaymentStatus
from tests.fakes import valid_request

REQUEST = valid_request(expiry_month=4, expiry_year=2099, cvv="012")
AUTHORIZED = {"authorized": True, "authorization_code": "abc"}
PAYMENT_ID = uuid4()


def bank_answering(handler) -> MountebankClient:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://bank")
    return MountebankClient(http_client)


def respond(status_code: int = 200, **content):
    return lambda _: httpx.Response(status_code, **content)


def fail_with(error_type: type[httpx.TransportError]):
    def handler(request: httpx.Request) -> httpx.Response:
        raise error_type("simulated failure", request=request)

    return handler


def recording_bank() -> tuple[MountebankClient, list[httpx.Request]]:
    received_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received_requests.append(request)
        return httpx.Response(200, json=AUTHORIZED)

    return bank_answering(handler), received_requests


async def test_sends_payment_in_bank_format():
    bank, received_requests = recording_bank()

    await bank.authorize(REQUEST, PAYMENT_ID)

    [sent] = received_requests
    assert sent.url.path == "/payments"
    assert json.loads(sent.content) == {
        "card_number": "2222405343248877",
        "expiry_date": "04/2099",
        "currency": "GBP",
        "amount": 100,
        "cvv": "012",
    }


@pytest.mark.parametrize(
    "authorized, expected_status",
    [(True, PaymentStatus.AUTHORIZED), (False, PaymentStatus.DECLINED)],
)
async def test_maps_bank_answer_to_status(authorized, expected_status):
    bank = bank_answering(respond(json={"authorized": authorized, "authorization_code": ""}))

    status = await bank.authorize(REQUEST, PAYMENT_ID)

    assert status == expected_status


@pytest.mark.parametrize(
    "handler",
    [
        respond(503),
        respond(400, json={"error_message": "missing field"}),
        respond(500),
        respond(text="not json"),
        respond(json={"unexpected": "shape"}),
        respond(json={"authorized": "yes"}),
        fail_with(httpx.ReadTimeout),
        fail_with(httpx.ConnectError),
    ],
    ids=["503", "400", "500", "not-json", "no-authorized", "non-bool", "timeout", "refused"],
)
async def test_bank_failures_raise_acquirer_unavailable(handler):
    with pytest.raises(AcquirerUnavailableError):
        await bank_answering(handler).authorize(REQUEST, PAYMENT_ID)


async def test_sends_payment_id_as_idempotency_key():
    bank, received_requests = recording_bank()

    await bank.authorize(REQUEST, PAYMENT_ID)

    assert received_requests[0].headers["Idempotency-Key"] == str(PAYMENT_ID)


@pytest.mark.parametrize(
    "handler, safe_to_retry",
    [
        (fail_with(httpx.ConnectError), True),
        (fail_with(httpx.ConnectTimeout), True),
        (fail_with(httpx.PoolTimeout), True),
        (respond(503), True),
        (fail_with(httpx.ReadTimeout), False),
        (fail_with(httpx.RemoteProtocolError), False),
        (respond(500), False),
        (respond(text="not json"), False),
    ],
    ids=["refused", "connect-timeout", "pool-timeout", "503", "read-timeout", "dropped", "500", "bad-body"],
)
async def test_marks_failure_safe_to_retry_only_when_bank_did_not_process(handler, safe_to_retry):
    with pytest.raises(AcquirerUnavailableError) as error:
        await bank_answering(handler).authorize(REQUEST, PAYMENT_ID)

    assert error.value.safe_to_retry is safe_to_retry
