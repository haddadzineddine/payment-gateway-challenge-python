import json
import logging

import httpx
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import format_span_id, format_trace_id
from prometheus_client import REGISTRY

from config import Settings
from observability import (
    configure_logging,
    create_tracer_provider,
    instrument_http_client,
    json_log_handler,
    otlp_log_handler,
    skip_unmonitored_access_logs,
)
from payments.domain.payment import PaymentStatus
from tests.fakes import VALID_PAYMENT


def test_log_line_is_json_with_trace_and_span_ids():
    handler = json_log_handler()
    tracer = create_tracer_provider(Settings(_env_file=None), "test").get_tracer(__name__)
    record = logging.LogRecord("payments", logging.INFO, __file__, 1, "payment_processed", None, None)
    record.payment_id = "7c4a1c2e"

    with tracer.start_as_current_span("POST /payments") as span:
        handler.filter(record)
        line = json.loads(handler.format(record))

    span_context = span.get_span_context()
    assert line["message"] == "payment_processed"
    assert line["level"] == "INFO"
    assert line["payment_id"] == "7c4a1c2e"
    assert line["trace_id"] == format_trace_id(span_context.trace_id)
    assert line["span_id"] == format_span_id(span_context.span_id)


def test_otlp_log_carries_fields_and_trace_context_once():
    exporter = InMemoryLogRecordExporter()
    logger_provider = LoggerProvider()
    logger_provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    handler = otlp_log_handler(logger_provider)
    tracer = create_tracer_provider(Settings(_env_file=None), "test").get_tracer(__name__)
    record = logging.LogRecord("payments", logging.INFO, __file__, 1, "payment_processed", None, None)
    record.payment_id = "7c4a1c2e"
    record.color_message = "\x1b[32mpayment_processed\x1b[0m"

    with tracer.start_as_current_span("POST /payments") as span:
        json_log_handler().filter(record)  # stdout handler ran first: trace_id is now a record field
        handler.handle(record)

    [exported] = [log.log_record for log in exporter.get_finished_logs()]
    assert exported.body == "payment_processed"
    assert exported.trace_id == span.get_span_context().trace_id
    assert exported.attributes == {"payment_id": "7c4a1c2e"}


def test_otlp_handler_is_on_root_logger_without_text_formatter():
    settings = Settings(_env_file=None, otel_exporter_otlp_logs_endpoint="http://localhost:3100/otlp/v1/logs")

    logger_provider = configure_logging(settings, "test")
    [otlp_handler] = [h for h in logging.getLogger().handlers if isinstance(h, LoggingHandler)]

    assert otlp_handler.formatter is None  # else the body becomes "INFO:logger:message"

    logger_provider.shutdown()
    configure_logging(Settings(_env_file=None), "test")


def test_access_log_skips_health_and_metrics_scrapes():
    configure_logging(Settings(_env_file=None), "test")

    assert skip_unmonitored_access_logs in logging.getLogger("uvicorn.access").filters
    assert not skip_unmonitored_access_logs(_access_record("/metrics"))
    assert not skip_unmonitored_access_logs(_access_record("/health?probe=1"))
    assert skip_unmonitored_access_logs(_access_record("/v1/payments"))


def _access_record(path: str) -> logging.LogRecord:
    # Same shape as uvicorn's access log call.
    args = ("127.0.0.1:5000", "GET", path, "1.1", 200)
    return logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d', args, None)


def test_otlp_exporter_own_logs_are_not_sent_back_to_it():
    exporter = InMemoryLogRecordExporter()
    logger_provider = LoggerProvider()
    logger_provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    record = logging.LogRecord("opentelemetry.exporter", logging.WARNING, __file__, 1, "export failed", None, None)

    otlp_log_handler(logger_provider).handle(record)

    assert exporter.get_finished_logs() == ()


async def test_outgoing_call_span_names_the_called_service_and_path():
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    transport = httpx.MockTransport(lambda request: httpx.Response(200))

    async with httpx.AsyncClient(base_url="http://bank:8080", transport=transport) as client:
        instrument_http_client(client, tracer_provider, peer_service="acquiring-bank")
        await client.post("/payments")

    [span] = exporter.get_finished_spans()
    assert span.name == "POST acquiring-bank /payments"  # not just "POST"
    assert span.attributes["peer.service"] == "acquiring-bank"


def test_metrics_count_payments_by_status(make_client):
    client, _, _ = make_client(PaymentStatus.DECLINED)
    declined_before = _payments("Declined")
    rejected_before = _payments("Rejected")

    client.post("/v1/payments", json=VALID_PAYMENT)
    client.post("/v1/payments", json={**VALID_PAYMENT, "cvv": "1"})
    metrics = client.get("/metrics")

    assert metrics.status_code == 200
    assert 'payments_total{status="Declined"}' in metrics.text
    assert "http_request_duration_seconds" in metrics.text
    assert _payments("Declined") == declined_before + 1
    assert _payments("Rejected") == rejected_before + 1


def test_replay_is_counted_apart_from_new_payments(make_client):
    client, _, _ = make_client(PaymentStatus.AUTHORIZED)
    headers = {"Idempotency-Key": "metrics-replay"}
    client.post("/v1/payments", json=VALID_PAYMENT, headers=headers)
    authorized_before = _payments("Authorized")
    replays_before = REGISTRY.get_sample_value("idempotent_replays_total") or 0

    client.post("/v1/payments", json=VALID_PAYMENT, headers=headers)

    assert _payments("Authorized") == authorized_before
    assert REGISTRY.get_sample_value("idempotent_replays_total") == replays_before + 1


def _payments(status: str) -> float:
    return REGISTRY.get_sample_value("payments_total", {"status": status}) or 0
