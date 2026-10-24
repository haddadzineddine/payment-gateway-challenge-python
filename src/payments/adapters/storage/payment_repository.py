from uuid import UUID

from payments.domain.payment import Payment


class InMemoryPaymentRepository:
    def __init__(self) -> None:
        self._payments: dict[UUID, Payment] = {}

    def add(self, payment: Payment) -> None:
        self._payments[payment.id] = payment

    def get(self, payment_id: UUID) -> Payment | None:
        return self._payments.get(payment_id)

    def remove(self, payment_id: UUID) -> None:
        self._payments.pop(payment_id, None)

    def count(self) -> int:
        return len(self._payments)
