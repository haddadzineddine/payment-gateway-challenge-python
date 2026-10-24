import logging

import httpx
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor, RequestInfo
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_fastapi_instrumentator import Instrumentator
from pythonjsonlogger.core import RESERVED_ATTRS
from pythonjsonlogger.json import JsonFormatter

from config import Settings

SERVICE_NAME = "payment-gateway"
UNMONITORED_PATHS = ["/health", "/metrics"]
UVICORN_LOGGERS = ["uvicorn", "uvicorn.error", "uvicorn.access"]
TERMINAL_COLORS_FIELD = "color_message"  # uvicorn's copy of the message with terminal colors
IGNORED_LOG_FIELDS = [*RESERVED_ATTRS, TERMINAL_COLORS_FIELD]
STDOUT_ONLY_LOG_FIELDS = ["trace_id", "span_id", TERMINAL_COLORS_FIELD]  # OTLP carries the trace context natively


def configure_logging(settings: Settings, service_version: str) -> LoggerProvider:
    """JSON logs on stdout. Also sent over OTLP if a logs endpoint is set."""
    logging.basicConfig(level=settings.log_level, handlers=[json_log_handler()], force=True)
    logger_provider = LoggerProvider(resource=_resource(service_version))

    # Added after basicConfig, which would give it a text formatter: the OTLP body must be the bare message.
    if settings.otel_exporter_otlp_logs_endpoint:
        exporter = OTLPLogExporter(endpoint=str(settings.otel_exporter_otlp_logs_endpoint))
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
        logging.getLogger().addHandler(otlp_log_handler(logger_provider))

    # uvicorn installs its own text handlers: send its logs through ours instead.
    for name in UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    # Prometheus scrapes every 5 s: one access log line each time would bury the real traffic.
    logging.getLogger("uvicorn.access").addFilter(skip_unmonitored_access_logs)

    return logger_provider


def skip_unmonitored_access_logs(record: logging.LogRecord) -> bool:
    _client, _method, path_with_query, *_ = record.args
    return path_with_query.split("?")[0] not in UNMONITORED_PATHS


def json_log_handler() -> logging.Handler:
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter(
            "{asctime}{levelname}{name}{message}",
            style="{",
            rename_fields={"asctime": "timestamp", "levelname": "level", "name": "logger"},
            reserved_attrs=IGNORED_LOG_FIELDS,
        )
    )
    handler.addFilter(add_trace_context)
    return handler


def otlp_log_handler(logger_provider: LoggerProvider) -> logging.Handler:
    handler = LoggingHandler(logger_provider=logger_provider)
    handler.addFilter(drop_stdout_only_fields)
    return handler


def drop_stdout_only_fields(record: logging.LogRecord) -> bool:
    # The exporter logs its own failures: sending those back to it would loop while the backend is down.
    if record.name.startswith("opentelemetry"):
        return False

    for field in STDOUT_ONLY_LOG_FIELDS:
        record.__dict__.pop(field, None)  # safe: the stdout handler sets them again, or already printed them

    return True


def add_trace_context(record: logging.LogRecord) -> bool:
    span_context = trace.get_current_span().get_span_context()

    if span_context.is_valid:
        record.trace_id = trace.format_trace_id(span_context.trace_id)
        record.span_id = trace.format_span_id(span_context.span_id)

    return True


def create_tracer_provider(settings: Settings, service_version: str) -> TracerProvider:
    """Trace ids are always created, for the logs. Spans are exported only if an OTLP endpoint is set."""
    tracer_provider = TracerProvider(resource=_resource(service_version))

    if settings.otel_exporter_otlp_endpoint:
        endpoint = f"{str(settings.otel_exporter_otlp_endpoint).rstrip('/')}/v1/traces"
        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))

    return tracer_provider


def _resource(service_version: str) -> Resource:
    return Resource.create({"service.name": SERVICE_NAME, "service.version": service_version})


def install_observability(app: FastAPI, tracer_provider: TracerProvider) -> None:
    """Traces and metrics for every request, plus the /health and /metrics endpoints."""
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=tracer_provider,
        excluded_urls=",".join(UNMONITORED_PATHS),
        exclude_spans=["receive", "send"],
    )

    Instrumentator(excluded_handlers=UNMONITORED_PATHS).instrument(app).expose(app, tags=["Observability"])

    @app.get("/health", tags=["Observability"])
    async def health() -> dict[str, str]:
        """Liveness: the process answers. The bank is not checked: a bank outage must not restart the gateway."""
        return {"status": "ok"}


def instrument_http_client(http_client: httpx.AsyncClient, tracer_provider: TracerProvider, peer_service: str) -> None:
    """Each call gets a client span named after the called service, e.g. "POST acquiring-bank /payments"."""

    async def name_span_after_peer(span: trace.Span, request: RequestInfo) -> None:
        span.update_name(f"{request.method.decode()} {peer_service} {request.url.path}")  # default is just "POST"
        span.set_attribute("peer.service", peer_service)

    HTTPXClientInstrumentor.instrument_client(
        http_client, tracer_provider=tracer_provider, request_hook=name_span_after_peer
    )
