import pytest
from fastapi.testclient import TestClient

from app import create_app
from config import Settings
from payments.adapters.api.routes import get_payment_service
from payments.adapters.storage.idempotency_store import InMemoryIdempotencyStore
from payments.adapters.storage.payment_repository import InMemoryPaymentRepository
from payments.application.payment_service import PaymentService
from payments.domain.payment import PaymentStatus
from tests.fakes import StubBank


@pytest.fixture(scope="module")
def app():
    # No .env: a local one must not change test results.
    return create_app(Settings(_env_file=None))


@pytest.fixture
def make_client(app):
    def build_client(bank_result=PaymentStatus.AUTHORIZED):
        bank = StubBank(bank_result)
        payments = InMemoryPaymentRepository()
        payment_service = PaymentService(bank, payments, InMemoryIdempotencyStore())

        app.dependency_overrides[get_payment_service] = lambda: payment_service
        return TestClient(app), bank, payments

    yield build_client

    app.dependency_overrides.clear()
