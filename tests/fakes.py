from datetime import UTC, datetime

from payments.domain.payment import Payment, PaymentRequest, PaymentStatus

NEXT_YEAR = datetime.now(UTC).year + 1

# Ends in an odd digit: the bank simulator authorizes it.
VALID_PAYMENT = {
    "card_number": "2222405343248877",
    "expiry_month": 4,
    "expiry_year": NEXT_YEAR,
    "currency": "GBP",
    "amount": 100,
    "cvv": "123",
}


def valid_request(**overrides) -> PaymentRequest:
    return PaymentRequest(**{**VALID_PAYMENT, **overrides})


def authorized_payment(**overrides) -> Payment:
    return Payment.from_request(valid_request(**overrides), PaymentStatus.AUTHORIZED)


def last_month() -> tuple[int, int]:
    """(month, year) of the previous month, in UTC."""
    today = datetime.now(UTC)

    if today.month == 1:
        return 12, today.year - 1

    return today.month - 1, today.year


class StubBank:
    def __init__(self, result: PaymentStatus | BaseException = PaymentStatus.AUTHORIZED):
        self.result = result
        self.calls = []
        self.payment_ids = []

    async def authorize(self, request, payment_id):
        self.calls.append(request)
        self.payment_ids.append(payment_id)

        if isinstance(self.result, BaseException):
            raise self.result

        return self.result


class FakeClock:
    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now
