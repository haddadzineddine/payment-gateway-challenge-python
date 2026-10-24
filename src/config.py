from typing import Literal

from pydantic import AnyHttpUrl, PositiveFloat
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Read from environment variables (BANK_URL -> bank_url) or .env, validated at startup."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    docs_enabled: bool = True

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    otel_exporter_otlp_endpoint: AnyHttpUrl | None = None
    otel_exporter_otlp_logs_endpoint: AnyHttpUrl | None = None  # full URL, as in the OTel spec

    bank_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8080")
    bank_timeout_seconds: PositiveFloat = 5.0
