from typing import Protocol
from uuid import UUID

from payments.domain.payment import Payment, PaymentRequest, PaymentStatus


class AcquiringBank(Protocol):
    async def authorize(self, request: PaymentRequest, payment_id: UUID) -> PaymentStatus:
        """Send the payment to the bank and return Authorized or Declined.

        The payment id is the bank's idempotency key: the bank can dedupe on it, and
        reconciliation can ask the bank about any payment by its id.
        Raises AcquirerUnavailableError if no clear answer came back; its
        safe_to_retry flag says whether the bank surely did not process it.
        """
        ...


class PaymentRepository(Protocol):
    def add(self, payment: Payment) -> None:
        """Save the payment under its id, replacing any payment with the same id."""
        ...

    def get(self, payment_id: UUID) -> Payment | None:
        """Return the payment with this id, or None if it does not exist."""
        ...

    def remove(self, payment_id: UUID) -> None:
        """Delete the payment, if it exists."""
        ...


class IdempotencyStore(Protocol):
    def reserve(self, key: str, request: PaymentRequest, payment_id: UUID) -> UUID | None:
        """Claim the key for this payment, before calling the bank.

        Returns None if the key is new (or expired): it now points to payment_id.
        Returns the earlier payment's id if the same request already used this key.
        Raises IdempotencyKeyReusedError if the key was used with a different request.
        """
        ...

    def release(self, key: str) -> None:
        """Forget the key: the bank surely did not process the payment, so a retry can run fresh."""
        ...
