class AcquirerUnavailableError(Exception):
    """The bank gave no usable answer. Never a decline: the card may have been charged,
    unless safe_to_retry says the bank certainly did not process the request."""

    def __init__(self, reason: str = "unavailable", *, safe_to_retry: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.safe_to_retry = safe_to_retry


class IdempotencyKeyReusedError(Exception):
    pass


class PaymentNotFoundError(Exception):
    pass
