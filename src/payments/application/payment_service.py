import logging
from dataclasses import dataclass
from uuid import UUID

from payments.application.ports import AcquiringBank, IdempotencyStore, PaymentRepository
from payments.domain.errors import AcquirerUnavailableError, PaymentNotFoundError
from payments.domain.payment import Payment, PaymentRequest, PaymentStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessedPayment:
    payment: Payment
    replayed: bool = False


class PaymentService:
    def __init__(
        self,
        bank: AcquiringBank,
        payment_repository: PaymentRepository,
        idempotency_store: IdempotencyStore,
    ) -> None:
        self._bank = bank
        self._payment_repository = payment_repository
        self._idempotency_store = idempotency_store

    async def process(self, request: PaymentRequest, idempotency_key: str | None = None) -> ProcessedPayment:
        pending_payment = Payment.from_request(request, PaymentStatus.PENDING)
        replayed_payment_id = self._reserve_idempotency_key(idempotency_key, request, pending_payment.id)

        if replayed_payment_id is not None:
            # Read it again: a Pending payment may have been settled since.
            processed = ProcessedPayment(self.get(replayed_payment_id), replayed=True)
        else:
            processed = ProcessedPayment(await self._authorize_with_bank(pending_payment, request, idempotency_key))

        _log_processed(processed)
        return processed

    def get(self, payment_id: UUID) -> Payment:
        payment = self._payment_repository.get(payment_id)

        if payment is None:
            raise PaymentNotFoundError

        return payment

    def _reserve_idempotency_key(
        self, idempotency_key: str | None, request: PaymentRequest, payment_id: UUID
    ) -> UUID | None:
        if idempotency_key is None:
            return None

        return self._idempotency_store.reserve(idempotency_key, request, payment_id)

    async def _authorize_with_bank(
        self, pending_payment: Payment, request: PaymentRequest, idempotency_key: str | None
    ) -> Payment:
        # Saved before the bank call: if the answer is lost (timeout, crash), the payment
        # still exists as Pending, and reconciliation can ask the bank about it by id.
        self._payment_repository.add(pending_payment)

        try:
            status = await self._bank.authorize(request, pending_payment.id)
        except AcquirerUnavailableError as error:
            if not error.safe_to_retry:
                return pending_payment  # the bank may have charged: stays Pending until reconciled

            self._undo(pending_payment, idempotency_key)
            raise

        payment = pending_payment.with_status(status)
        self._payment_repository.add(payment)
        return payment

    def _undo(self, pending_payment: Payment, idempotency_key: str | None) -> None:
        """The bank surely did not process the payment: forget it, so a retry starts fresh."""
        self._payment_repository.remove(pending_payment.id)

        if idempotency_key is not None:
            self._idempotency_store.release(idempotency_key)


def _log_processed(processed: ProcessedPayment) -> None:
    payment = processed.payment
    logger.info(
        "payment_processed",
        extra={
            "payment_id": str(payment.id),
            "status": payment.status.value,
            "amount": payment.amount,
            "currency": payment.currency,
            "replayed": processed.replayed,
        },
    )
