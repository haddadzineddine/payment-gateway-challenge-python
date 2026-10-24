from uuid import uuid4

from payments.adapters.storage.payment_repository import InMemoryPaymentRepository
from payments.domain.payment import PaymentStatus
from tests.fakes import authorized_payment


def test_added_payment_can_be_fetched_by_id():
    repository = InMemoryPaymentRepository()
    payment = authorized_payment()

    repository.add(payment)

    assert repository.get(payment.id) == payment


def test_unknown_id_returns_none():
    repository = InMemoryPaymentRepository()

    assert repository.get(uuid4()) is None


def test_adding_same_id_replaces_payment():
    repository = InMemoryPaymentRepository()
    payment = authorized_payment()
    declined = payment.model_copy(update={"status": PaymentStatus.DECLINED})
    repository.add(payment)

    repository.add(declined)

    assert repository.get(payment.id) == declined
    assert repository.count() == 1


def test_removed_payment_is_gone():
    repository = InMemoryPaymentRepository()
    payment = authorized_payment()
    repository.add(payment)

    repository.remove(payment.id)

    assert repository.get(payment.id) is None
    assert repository.count() == 0


def test_removing_unknown_id_is_a_no_op():
    repository = InMemoryPaymentRepository()
    repository.add(authorized_payment())

    repository.remove(uuid4())

    assert repository.count() == 1
