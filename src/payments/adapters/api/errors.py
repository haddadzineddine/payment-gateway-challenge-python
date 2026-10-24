from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from payments.domain.errors import AcquirerUnavailableError, IdempotencyKeyReusedError, PaymentNotFoundError
from payments.domain.payment import PaymentStatus


class FieldError(BaseModel):
    field: str = Field(examples=["cvv"])
    message: str = Field(examples=["String should match pattern '^[0-9]{3,4}$'"])


class RejectedResponse(BaseModel):
    """Invalid input: no payment created, bank not called."""

    status: Literal[PaymentStatus.REJECTED] = PaymentStatus.REJECTED
    errors: list[FieldError]


class ErrorResponse(BaseModel):
    error: str = Field(examples=["Acquiring bank unavailable"])


ERROR_RESPONSES: dict[type[Exception], tuple[int, str]] = {
    PaymentNotFoundError: (404, "Payment not found"),
    IdempotencyKeyReusedError: (409, "Idempotency-Key reused with a different request"),
    AcquirerUnavailableError: (502, "Acquiring bank unavailable"),
}


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _reject_invalid_request)

    for error_type, (status_code, message) in ERROR_RESPONSES.items():
        app.add_exception_handler(error_type, _respond_with_error(status_code, message))

    _hide_default_422(app)


async def _reject_invalid_request(_: Request, error: RequestValidationError) -> JSONResponse:
    # The message only, never the input: it may contain card data.
    field_errors = [FieldError(field=_field_name(detail["loc"]), message=detail["msg"]) for detail in error.errors()]

    return JSONResponse(status_code=400, content=RejectedResponse(errors=field_errors).model_dump(mode="json"))


def _field_name(location: tuple[str | int, ...]) -> str:
    return ".".join(str(part) for part in location[1:]) or "body"


def _respond_with_error(status_code: int, message: str):
    async def handle(_: Request, __: Exception) -> JSONResponse:
        return JSONResponse(status_code=status_code, content=ErrorResponse(error=message).model_dump())

    return handle


def _hide_default_422(app: FastAPI) -> None:
    # FastAPI documents a 422 on every route, but invalid input is answered with 400 Rejected.
    generate_openapi = app.openapi

    def openapi() -> dict:
        schema = generate_openapi()
        for path in schema["paths"].values():
            for operation in path.values():
                operation["responses"].pop("422", None)

        unused_schemas = schema.get("components", {}).get("schemas", {})
        unused_schemas.pop("HTTPValidationError", None)
        unused_schemas.pop("ValidationError", None)
        return schema

    app.openapi = openapi
