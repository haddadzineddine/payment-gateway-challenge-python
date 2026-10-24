import logging
from uuid import UUID

import httpx

from payments.domain.errors import AcquirerUnavailableError
from payments.domain.payment import PaymentRequest, PaymentStatus

logger = logging.getLogger(__name__)

NEVER_SENT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


class MountebankClient:
    """AcquiringBank over the bank simulator's HTTP API. One attempt, no retries."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http_client = http_client

    async def authorize(self, request: PaymentRequest, payment_id: UUID) -> PaymentStatus:
        try:
            response = await self._post_payment(request, payment_id)
            return _read_bank_answer(response)
        except AcquirerUnavailableError as error:
            logger.warning(
                "bank_unavailable",
                extra={"payment_id": str(payment_id), "reason": error.reason, "error": repr(error.__cause__)},
            )
            raise

    async def _post_payment(self, request: PaymentRequest, payment_id: UUID) -> httpx.Response:
        headers = {"Idempotency-Key": str(payment_id)}

        try:
            return await self._http_client.post("/payments", json=_to_bank_format(request), headers=headers)
        # Before TimeoutException: ConnectTimeout and PoolTimeout are subclasses of it.
        except NEVER_SENT_ERRORS as error:
            raise AcquirerUnavailableError("not_sent", safe_to_retry=True) from error
        except httpx.TimeoutException as error:
            raise AcquirerUnavailableError("timeout") from error
        except httpx.HTTPError as error:
            raise AcquirerUnavailableError("transport_error") from error


def _to_bank_format(request: PaymentRequest) -> dict:
    return {
        "card_number": request.card_number,
        "expiry_date": f"{request.expiry_month:02d}/{request.expiry_year}",
        "currency": request.currency,
        "amount": request.amount,
        "cvv": request.cvv,
    }


def _read_bank_answer(response: httpx.Response) -> PaymentStatus:
    if response.status_code == httpx.codes.SERVICE_UNAVAILABLE:
        raise AcquirerUnavailableError("unavailable", safe_to_retry=True)

    try:
        response.raise_for_status()
        authorized = response.json()["authorized"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
        raise AcquirerUnavailableError("bad_response") from error

    if not isinstance(authorized, bool):
        raise AcquirerUnavailableError("bad_response")

    return PaymentStatus.AUTHORIZED if authorized else PaymentStatus.DECLINED
