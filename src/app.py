from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx
from fastapi import FastAPI
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk.trace import TracerProvider

from config import Settings
from observability import configure_logging, create_tracer_provider, install_observability, instrument_http_client
from payments.adapters.api.routes import install_payment_api
from payments.adapters.bank.mountebank import MountebankClient
from payments.adapters.storage.idempotency_store import InMemoryIdempotencyStore
from payments.adapters.storage.payment_repository import InMemoryPaymentRepository
from payments.application.payment_service import PaymentService

API_TITLE = "Payment Gateway API"
API_VERSION = "1.0.0"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    logger_provider = configure_logging(settings, API_VERSION)
    tracer_provider = create_tracer_provider(settings, API_VERSION)

    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        lifespan=_lifespan(settings, tracer_provider, logger_provider),
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )

    install_payment_api(app)
    install_observability(app, tracer_provider)

    return app


def _lifespan(
    settings: Settings, tracer_provider: TracerProvider, logger_provider: LoggerProvider
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        async with httpx.AsyncClient(
            base_url=str(settings.bank_url),
            timeout=settings.bank_timeout_seconds,
        ) as bank_http_client:
            instrument_http_client(bank_http_client, tracer_provider, peer_service="acquiring-bank")

            app.state.payment_service = PaymentService(
                bank=MountebankClient(bank_http_client),
                payment_repository=InMemoryPaymentRepository(),
                idempotency_store=InMemoryIdempotencyStore(),
            )
            yield

        tracer_provider.shutdown()  # flushes buffered spans
        logger_provider.shutdown()  # and buffered logs

    return lifespan
