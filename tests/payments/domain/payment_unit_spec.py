from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from payments.domain.payment import Payment, PaymentRequest, PaymentStatus
from tests.fakes import NEXT_YEAR, VALID_PAYMENT, last_month, valid_request

LAST_MONTH, LAST_MONTHS_YEAR = last_month()


def test_valid_request_is_accepted():
    request = PaymentRequest(**VALID_PAYMENT)

    assert request.card_number == VALID_PAYMENT["card_number"]


def test_card_expiring_this_month_is_accepted():
    today = datetime.now(UTC)

    request = valid_request(expiry_month=today.month, expiry_year=today.year)

    assert (request.expiry_month, request.expiry_year) == (today.month, today.year)


def test_cvv_leading_zero_is_preserved():
    assert valid_request(cvv="012").cvv == "012"


@pytest.mark.parametrize(
    "invalid_fields",
    [
        pytest.param({"card_number": "2222405343248"}, id="card-13-digits"),
        pytest.param({"card_number": "22224053432488771234"}, id="card-20-digits"),
        pytest.param({"card_number": "2222abcd43248877"}, id="card-letters"),
        pytest.param({"card_number": "2222 4053 4324 8877"}, id="card-spaces"),
        pytest.param({"card_number": "٢٢٢٢٤٠٥٣٤٣٢٤٨٨٧٧"}, id="card-non-ascii-digits"),
        pytest.param({"card_number": 2222405343248877}, id="card-as-number"),
        pytest.param({"expiry_month": 0}, id="month-0"),
        pytest.param({"expiry_month": 13}, id="month-13"),
        pytest.param({"expiry_month": "4"}, id="month-as-string"),
        pytest.param({"expiry_year": 27}, id="year-2-digits"),
        pytest.param({"expiry_year": 10000}, id="year-5-digits"),
        pytest.param({"expiry_year": NEXT_YEAR - 5}, id="year-in-past"),
        pytest.param({"expiry_month": LAST_MONTH, "expiry_year": LAST_MONTHS_YEAR}, id="expired-last-month"),
        pytest.param({"currency": "JPY"}, id="currency-unsupported"),
        pytest.param({"currency": "gbp"}, id="currency-lowercase"),
        pytest.param({"currency": "GBPX"}, id="currency-4-letters"),
        pytest.param({"amount": 0}, id="amount-zero"),
        pytest.param({"amount": -100}, id="amount-negative"),
        pytest.param({"amount": "100"}, id="amount-as-string"),
        pytest.param({"amount": 100.5}, id="amount-decimal"),
        pytest.param({"amount": 100.0}, id="amount-float"),
        pytest.param({"amount": True}, id="amount-bool"),
        pytest.param({"cvv": "12"}, id="cvv-2-digits"),
        pytest.param({"cvv": "12345"}, id="cvv-5-digits"),
        pytest.param({"cvv": "12a"}, id="cvv-letter"),
        pytest.param({"cvv": 123}, id="cvv-as-number"),
    ],
)
def test_invalid_field_is_refused(invalid_fields):
    with pytest.raises(ValidationError) as error:
        valid_request(**invalid_fields)

    refused_fields = {detail["loc"][0] for detail in error.value.errors()}
    assert refused_fields <= set(invalid_fields)


@pytest.mark.parametrize("missing_field", list(VALID_PAYMENT))
def test_missing_field_is_refused(missing_field):
    payload = {field: value for field, value in VALID_PAYMENT.items() if field != missing_field}

    with pytest.raises(ValidationError) as error:
        PaymentRequest(**payload)

    assert [detail["loc"][0] for detail in error.value.errors()] == [missing_field]


def test_payment_keeps_only_last_four_digits():
    payment = Payment.from_request(valid_request(), PaymentStatus.AUTHORIZED)

    assert payment.last_four == "8877"
    assert VALID_PAYMENT["card_number"] not in payment.model_dump_json()
    assert VALID_PAYMENT["cvv"] not in payment.model_dump_json(exclude={"id"})


def test_each_payment_gets_a_unique_id():
    first = Payment.from_request(valid_request(), PaymentStatus.AUTHORIZED)
    second = Payment.from_request(valid_request(), PaymentStatus.AUTHORIZED)

    assert first.id != second.id


def test_with_status_returns_a_new_payment_with_the_same_id():
    pending = Payment.from_request(valid_request(), PaymentStatus.PENDING)

    authorized = pending.with_status(PaymentStatus.AUTHORIZED)

    assert authorized.status == PaymentStatus.AUTHORIZED
    assert authorized.id == pending.id
    assert pending.status == PaymentStatus.PENDING
