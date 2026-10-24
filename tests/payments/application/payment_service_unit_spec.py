import logging
from uuid import uuid4

import pytest

from payments.adapters.storage.idempotency_store import InMemoryIdempotencyStore
from payments.adapters.storage.payment_repository import InMemoryPaymentRepository
from payments.application.payment_service import PaymentService
from payments.domain.errors import AcquirerUnavailableError, IdempotencyKeyReusedError, PaymentNotFoundError
from payments.domain.payment import PaymentStatus
from tests.fakes import StubBank, authorized_payment, valid_request

IDEMPOTENCY_KEY = "order-42-attempt-1"
BANK_NOT_REACHED = AcquirerUnavailableError("not_sent", safe_to_retry=True)
BANK_ANSWER_LOST = AcquirerUnavailableError("timeout")


def make_service(bank_result=PaymentStatus.AUTHORIZED):
    bank = StubBank(bank_result)
    payments = InMemoryPaymentRepository()
    service = PaymentService(bank, payments, InMemoryIdempotencyStore())

    return service, bank, payments


@pytest.mark.parametrize("status", [PaymentStatus.AUTHORIZED, PaymentStatus.DECLINED])
async def test_bank_answer_is_stored(status):
    service, _, payments = make_service(status)

    processed = await service.process(valid_request())

    assert processed.payment.status == status
    assert processed.replayed is False
    assert payments.get(processed.payment.id) == processed.payment


async def test_payment_id_is_sent_to_the_bank():
    service, bank, _ = make_service()

    processed = await service.process(valid_request())

    assert bank.payment_ids == [processed.payment.id]


async def test_payment_is_saved_as_pending_before_the_bank_is_called():
    service, bank, payments = make_service()
    seen_by_bank = []
    authorize = bank.authorize

    async def authorize_and_look(request, payment_id):
        seen_by_bank.append(payments.get(payment_id))
        return await authorize(request, payment_id)

    bank.authorize = authorize_and_look

    await service.process(valid_request())

    assert seen_by_bank[0].status == PaymentStatus.PENDING


async def test_lost_bank_answer_keeps_the_payment_pending():
    service, _, payments = make_service(BANK_ANSWER_LOST)

    processed = await service.process(valid_request())

    assert processed.payment.status == PaymentStatus.PENDING
    assert payments.get(processed.payment.id) == processed.payment


async def test_bank_not_reached_stores_nothing():
    service, _, payments = make_service(BANK_NOT_REACHED)

    with pytest.raises(AcquirerUnavailableError):
        await service.process(valid_request())

    assert payments.count() == 0


async def test_crash_during_the_bank_call_leaves_the_payment_pending():
    service, _, payments = make_service(RuntimeError("bug"))

    with pytest.raises(RuntimeError):
        await service.process(valid_request())

    assert payments.count() == 1


async def test_without_key_every_request_reaches_bank():
    service, bank, _ = make_service()

    await service.process(valid_request())
    await service.process(valid_request())

    assert len(bank.calls) == 2
    assert len(set(bank.payment_ids)) == 2


async def test_same_key_replays_without_calling_bank():
    service, bank, payments = make_service()

    first = await service.process(valid_request(), IDEMPOTENCY_KEY)
    retry = await service.process(valid_request(), IDEMPOTENCY_KEY)

    assert retry.payment == first.payment
    assert retry.replayed is True
    assert len(bank.calls) == 1
    assert payments.count() == 1


async def test_same_key_with_different_request_is_refused():
    service, bank, _ = make_service()
    await service.process(valid_request(), IDEMPOTENCY_KEY)

    with pytest.raises(IdempotencyKeyReusedError):
        await service.process(valid_request(amount=999), IDEMPOTENCY_KEY)

    assert len(bank.calls) == 1


async def test_key_is_released_when_the_bank_certainly_did_not_charge():
    service, bank, _ = make_service(BANK_NOT_REACHED)
    with pytest.raises(AcquirerUnavailableError):
        await service.process(valid_request(), IDEMPOTENCY_KEY)
    bank.result = PaymentStatus.AUTHORIZED

    retry = await service.process(valid_request(), IDEMPOTENCY_KEY)

    assert retry.replayed is False
    assert retry.payment.status == PaymentStatus.AUTHORIZED
    assert len(bank.calls) == 2


async def test_retry_after_a_lost_answer_replays_the_pending_payment_without_calling_bank():
    service, bank, _ = make_service(BANK_ANSWER_LOST)
    first = await service.process(valid_request(), IDEMPOTENCY_KEY)
    bank.result = PaymentStatus.AUTHORIZED

    retry = await service.process(valid_request(), IDEMPOTENCY_KEY)

    assert retry.replayed is True
    assert retry.payment == first.payment
    assert retry.payment.status == PaymentStatus.PENDING
    assert len(bank.calls) == 1


async def test_replay_returns_the_current_status_once_settled():
    service, _, payments = make_service(BANK_ANSWER_LOST)
    pending = (await service.process(valid_request(), IDEMPOTENCY_KEY)).payment
    payments.add(pending.with_status(PaymentStatus.AUTHORIZED))  # as reconciliation would

    retry = await service.process(valid_request(), IDEMPOTENCY_KEY)

    assert retry.payment.status == PaymentStatus.AUTHORIZED


async def test_logs_processed_payment_without_card_data(caplog):
    caplog.set_level(logging.INFO)
    service, _, _ = make_service()

    processed = await service.process(valid_request(), IDEMPOTENCY_KEY)

    [record] = [record for record in caplog.records if record.message == "payment_processed"]
    assert record.payment_id == str(processed.payment.id)
    assert (record.status, record.amount, record.currency, record.replayed) == ("Authorized", 100, "GBP", False)
    assert valid_request().card_number not in caplog.text
    assert all(valid_request().card_number not in str(value) for value in vars(record).values())


def test_get_returns_stored_payment():
    service, _, payments = make_service()
    payment = authorized_payment()
    payments.add(payment)

    found = service.get(payment.id)

    assert found == payment


def test_get_unknown_id_raises_not_found():
    service, _, _ = make_service()

    with pytest.raises(PaymentNotFoundError):
        service.get(uuid4())
