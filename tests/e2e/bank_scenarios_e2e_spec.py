"""End-to-end bank failures: programs the simulator through Mountebank's API (:2525) to make the bank
slow, broken or unavailable for one test's payments. Skipped if the simulator is down."""

import json
import random
from collections.abc import Iterator, Sequence
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app import create_app
from config import Settings
from tests.fakes import NEXT_YEAR, VALID_PAYMENT

MOUNTEBANK_URL = "http://localhost:2525"
BANK_IMPOSTER = "/imposters/8080"
BANK_TIMEOUT_SECONDS = 0.5


def _mountebank_running() -> bool:
    try:
        return httpx.get(f"{MOUNTEBANK_URL}{BANK_IMPOSTER}", timeout=0.5).is_success
    except httpx.HTTPError:
        return False


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not _mountebank_running(), reason="bank simulator not running (docker-compose up -d)"),
]


def bank_answer(body: Any, status_code: int = 200, delay_seconds: float = 0) -> dict[str, Any]:
    answer: dict[str, Any] = {
        "is": {"statusCode": status_code, "headers": {"Content-Type": "application/json"}, "body": body}
    }
    if delay_seconds:
        answer["behaviors"] = [{"wait": int(delay_seconds * 1000)}]

    return answer


AUTHORIZED = bank_answer({"authorized": True, "authorization_code": "0bb07405"})


class ProgrammedBank:
    """Stubs added to the bank simulator through Mountebank's API. They match only this test's
    amount (random), so every other request still gets the simulator's own answers."""

    def __init__(self, mountebank: httpx.Client) -> None:
        self._mountebank = mountebank
        self.amount = random.randint(100_000, 999_999_999)
        self.idempotency_key = f"e2e-{uuid4()}"

    def answers(self, *answers: dict[str, Any], also_matching: Sequence[dict[str, Any]] = ()) -> None:
        predicates = [{"equals": {"body": {"amount": self.amount}}}, *also_matching]
        stub = {"predicates": predicates, "responses": list(answers)}
        self._mountebank.post(f"{BANK_IMPOSTER}/stubs", json={"index": 0, "stub": stub}).raise_for_status()

    def remove_stubs(self) -> None:
        stubs = self._mountebank.get(BANK_IMPOSTER).json()["stubs"]

        for index in reversed(range(len(stubs))):
            if f'"amount": {self.amount}' in json.dumps(stubs[index].get("predicates", [])):
                self._mountebank.delete(f"{BANK_IMPOSTER}/stubs/{index}").raise_for_status()


@pytest.fixture
def bank() -> Iterator[ProgrammedBank]:
    with httpx.Client(base_url=MOUNTEBANK_URL) as mountebank:
        programmed_bank = ProgrammedBank(mountebank)
        yield programmed_bank

        programmed_bank.remove_stubs()


@pytest.fixture
def pay(bank: ProgrammedBank) -> Iterator:
    settings = Settings(_env_file=None, bank_timeout_seconds=BANK_TIMEOUT_SECONDS)
    payment = {**VALID_PAYMENT, "amount": bank.amount}

    with TestClient(create_app(settings)) as gateway:
        yield lambda: gateway.post("/v1/payments", json=payment, headers={"Idempotency-Key": bank.idempotency_key})


def test_payment_reaches_the_bank_in_its_wire_format(bank, pay):
    bank.answers(bank_answer({"error": "unexpected request"}, status_code=400))
    bank.answers(
        AUTHORIZED,
        also_matching=[
            {
                "deepEquals": {
                    "body": {
                        "card_number": "2222405343248877",
                        "expiry_date": f"04/{NEXT_YEAR}",
                        "currency": "GBP",
                        "amount": bank.amount,
                        "cvv": "123",
                    }
                }
            },
            {"matches": {"headers": {"Idempotency-Key": "^[0-9a-f-]{36}$"}}},  # our payment id
        ],
    )

    response = pay()

    assert response.status_code == 201
    assert response.json()["status"] == "Authorized"


def test_declined_by_the_bank(bank, pay):
    bank.answers(bank_answer({"authorized": False, "authorization_code": ""}))

    response = pay()

    assert response.status_code == 201
    assert response.json()["status"] == "Declined"


def test_bank_unavailable_then_the_retry_with_the_same_key_reaches_the_bank_again(bank, pay):
    bank.answers(bank_answer({}, status_code=503), AUTHORIZED)

    failed = pay()
    retried = pay()

    assert failed.status_code == 502
    assert retried.status_code == 201
    assert retried.json()["status"] == "Authorized"


def test_slow_bank_leaves_the_payment_pending_and_the_retry_never_reaches_the_bank(bank, pay):
    # If the retry reached the bank, the second answer would make it Authorized.
    bank.answers(bank_answer({"authorized": True}, delay_seconds=BANK_TIMEOUT_SECONDS * 2), AUTHORIZED)

    timed_out = pay()
    retried = pay()

    assert timed_out.status_code == 202
    assert timed_out.json()["status"] == "Pending"
    assert retried.status_code == 202
    assert retried.json() == timed_out.json()


@pytest.mark.parametrize(
    ("status_code", "body"),
    [
        (500, {"error": "boom"}),
        (400, {"error": "missing field"}),
        (200, "not json"),
        (200, {"authorized": "yes"}),
        (200, {"authorization_code": "0bb07405"}),
    ],
    ids=["500", "400", "not-json", "non-boolean", "no-authorized-field"],
)
def test_unusable_bank_answer_leaves_the_payment_pending(bank, pay, status_code, body):
    bank.answers(bank_answer(body, status_code=status_code))

    response = pay()

    assert response.status_code == 202
    assert response.json()["status"] == "Pending"
