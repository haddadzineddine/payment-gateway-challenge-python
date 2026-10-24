import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx
from fastapi import FastAPI

from config import Settings
from payments.adapters.api.routes import install_payment_api
from payments.adapters.bank.mountebank import MountebankClient
from payments.adapters.storage.idempotency_store import InMemoryIdempotencyStore
from payments.adapters.storage.payment_repository import InMemoryPaymentRepository
from payments.application.payment_service import PaymentService

API_TITLE = "Payment Gateway API"
API_VERSION = "1.0.0"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        lifespan=_lifespan(settings),
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )

    install_payment_api(app)

    return app


def _lifespan(settings: Settings) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        async with httpx.AsyncClient(
            base_url=str(settings.bank_url),
            timeout=settings.bank_timeout_seconds,
        ) as bank_http_client:
            app.state.payment_service = PaymentService(
                bank=MountebankClient(bank_http_client),
                payment_repository=InMemoryPaymentRepository(),
                idempotency_store=InMemoryIdempotencyStore(),
            )
            yield

    return lifespan
