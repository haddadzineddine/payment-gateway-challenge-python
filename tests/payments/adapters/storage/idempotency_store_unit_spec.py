from uuid import uuid4

import pytest

from payments.adapters.storage.idempotency_store import InMemoryIdempotencyStore
from payments.domain.errors import IdempotencyKeyReusedError
from tests.fakes import FakeClock, valid_request

KEY = "order-42"
REQUEST = valid_request()
OTHER_REQUEST = valid_request(amount=999)
PAYMENT_ID = uuid4()
OTHER_PAYMENT_ID = uuid4()


def test_new_key_is_reserved_then_points_to_its_payment():
    store = InMemoryIdempotencyStore()

    reserved = store.reserve(KEY, REQUEST, PAYMENT_ID)
    retried = store.reserve(KEY, REQUEST, OTHER_PAYMENT_ID)

    assert reserved is None
    assert retried == PAYMENT_ID


def test_reuse_with_different_request_is_refused():
    store = InMemoryIdempotencyStore()
    store.reserve(KEY, REQUEST, PAYMENT_ID)

    with pytest.raises(IdempotencyKeyReusedError):
        store.reserve(KEY, OTHER_REQUEST, OTHER_PAYMENT_ID)


def test_released_key_can_be_used_again():
    store = InMemoryIdempotencyStore()
    store.reserve(KEY, REQUEST, PAYMENT_ID)

    store.release(KEY)

    assert store.reserve(KEY, REQUEST, OTHER_PAYMENT_ID) is None


def test_key_expires_after_ttl():
    clock = FakeClock()
    store = InMemoryIdempotencyStore(ttl_seconds=60, clock=clock)
    store.reserve(KEY, REQUEST, PAYMENT_ID)

    clock.now = 59
    retried_before_expiry = store.reserve(KEY, REQUEST, OTHER_PAYMENT_ID)

    clock.now = 60
    reserved_after_expiry = store.reserve(KEY, REQUEST, OTHER_PAYMENT_ID)

    assert retried_before_expiry == PAYMENT_ID
    assert reserved_after_expiry is None


def test_store_does_not_keep_card_data():
    store = InMemoryIdempotencyStore()

    store.reserve(KEY, REQUEST, PAYMENT_ID)

    assert REQUEST.card_number not in repr(store._records)
