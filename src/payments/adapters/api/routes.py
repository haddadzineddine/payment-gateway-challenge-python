from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Header, Request, Response
from opentelemetry import trace

from payments.adapters.api.errors import ErrorResponse, RejectedResponse, install_error_handlers
from payments.adapters.metrics import IDEMPOTENT_REPLAYS, PAYMENTS
from payments.application.payment_service import PaymentService
from payments.domain.errors import PaymentNotFoundError
from payments.domain.payment import Payment, PaymentRequest, PaymentStatus

API_VERSION_PREFIX = "/v1"
IDEMPOTENCY_KEY_PATTERN = r"^[\x21-\x7e]{1,255}$"  # printable ASCII, no spaces


router = APIRouter(prefix="/payments", tags=["Payments"])


def install_payment_api(app: FastAPI) -> None:
    app.include_router(router, prefix=API_VERSION_PREFIX)
    install_error_handlers(app)


def get_payment_service(request: Request) -> PaymentService:
    return request.app.state.payment_service


PaymentServiceDependency = Annotated[PaymentService, Depends(get_payment_service)]


@router.post(
    "",
    status_code=201,
    responses={
        202: {"model": Payment, "description": "Bank answer lost: the payment is Pending, follow it with GET"},
        400: {"model": RejectedResponse, "description": "Invalid input: bank not called, nothing stored"},
        409: {"model": ErrorResponse, "description": "Idempotency-Key reused with a different request"},
        502: {"model": ErrorResponse, "description": "Bank certainly did not get the request: nothing stored"},
    },
)
async def create_payment(
    payment_request: PaymentRequest,
    request: Request,
    response: Response,
    payment_service: PaymentServiceDependency,
    idempotency_key: Annotated[str | None, Header(pattern=IDEMPOTENCY_KEY_PATTERN)] = None,
) -> Payment:
    processed = await payment_service.process(payment_request, idempotency_key)
    payment = processed.payment

    response.headers["Location"] = request.app.url_path_for("get_payment", payment_id=str(payment.id))

    if payment.status == PaymentStatus.PENDING:
        response.status_code = 202

    if processed.replayed:
        response.headers["Idempotent-Replayed"] = "true"
        IDEMPOTENT_REPLAYS.inc()
    else:
        PAYMENTS.labels(status=payment.status.value).inc()

    _add_payment_to_trace(payment, processed.replayed)
    return payment


@router.get(
    "/{payment_id}",
    responses={404: {"model": ErrorResponse, "description": "Unknown or malformed id"}},
)
async def get_payment(
    # A str, not a UUID: a malformed id is 404 like an unknown one, never 400 Rejected.
    payment_id: str,
    response: Response,
    payment_service: PaymentServiceDependency,
) -> Payment:
    payment = payment_service.get(_parse_payment_id(payment_id))

    response.headers["Cache-Control"] = "no-store"
    return payment


def _add_payment_to_trace(payment: Payment, replayed: bool) -> None:
    span = trace.get_current_span()
    span.set_attribute("payment.id", str(payment.id))
    span.set_attribute("payment.status", payment.status.value)
    span.set_attribute("payment.replayed", replayed)


def _parse_payment_id(payment_id: str) -> UUID:
    try:
        return UUID(payment_id)
    except ValueError:
        raise PaymentNotFoundError from None
