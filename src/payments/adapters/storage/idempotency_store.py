import hashlib
import hmac
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from payments.domain.errors import IdempotencyKeyReusedError
from payments.domain.payment import PaymentRequest

KEY_TTL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class _KeyRecord:
    request_fingerprint: str
    payment_id: UUID
    reserved_at: float


class InMemoryIdempotencyStore:
    def __init__(
        self,
        ttl_seconds: float = KEY_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._records: dict[str, _KeyRecord] = {}
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        # HMAC, not a plain hash: a SHA-256 of a card number can be brute-forced.
        self._fingerprint_secret = secrets.token_bytes(32)

    def reserve(self, key: str, request: PaymentRequest, payment_id: UUID) -> UUID | None:
        # No await in here: two concurrent requests with one key can never both reserve it.
        fingerprint = self._fingerprint(request)
        record = self._unexpired_record(key)

        if record is None:
            self._records[key] = _KeyRecord(fingerprint, payment_id, reserved_at=self._clock())
            return None

        if record.request_fingerprint != fingerprint:
            raise IdempotencyKeyReusedError

        return record.payment_id

    def release(self, key: str) -> None:
        self._records.pop(key, None)

    def _unexpired_record(self, key: str) -> _KeyRecord | None:
        record = self._records.get(key)

        if record is None or self._clock() - record.reserved_at >= self._ttl_seconds:
            return None

        return record

    def _fingerprint(self, request: PaymentRequest) -> str:
        body = request.model_dump_json().encode()
        return hmac.new(self._fingerprint_secret, body, hashlib.sha256).hexdigest()
