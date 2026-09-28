"""
Configuration management using Pydantic Settings
"""
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings

#: Shipped as ``.env.example``. Read so a deployment can be checked against the
#: documented surface without a running process, and so a variable added to
#: :class:`Settings` without a template entry is a visible omission.
ENV_TEMPLATE = Path(__file__).resolve().parents[2] / ".env.example"

#: The development signing key, named here so the production check compares
#: against the one value that matters rather than re-typing a literal.
DEV_JWT_SECRET = "dev-secret-change-in-production"


class Settings(BaseSettings):
    # Application
    APP_NAME: str = "BoQ Price Forecast"
    VERSION: str = "0.1.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    LOG_LEVEL: str = "DEBUG"
    #: Send Strict-Transport-Security. Set this rather than keying off
    #: ``ENVIRONMENT``: a production deployment may terminate TLS in front of
    #: this process, and a staging one may not. The header pins the *hostname*,
    #: so sending it over plain http breaks the next plain-http request to it.
    FORCE_HTTPS: bool = False
    #: Honour ``X-Forwarded-For`` when deriving a client identity. Off unless a
    #: proxy is known to be in front: the header is client-controlled, and
    #: trusting it with nothing in front is a rate-limit bypass.
    TRUST_PROXY_HEADERS: bool = False
    #: Hosts the service will answer for. Empty means "any", which is right in
    #: development and wrong in production.
    ALLOWED_HOSTS: List[str] = []

    # Database
    DATABASE_URL: str = "postgresql+psycopg2://boq_user:boq_password@localhost:5432/boq_price_forecast"
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Celery
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/0"

    # Security
    JWT_SECRET_KEY: str = DEV_JWT_SECRET
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # OpenRouter / LLM
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    DEFAULT_LLM_MODEL: str = "openrouter/auto"
    LLM_TIMEOUT_SECONDS: int = 30
    LLM_MAX_RETRIES: int = 3
    #: Base for the exponential backoff between retry attempts, in seconds. The
    #: first retry waits this long, the second twice, and so on.
    LLM_RETRY_BASE_SECONDS: float = 1.0
    #: Ceiling on that backoff, so a long outage does not push the last attempt
    #: past the caller's own deadline.
    LLM_RETRY_MAX_SECONDS: float = 30.0
    #: Attribution headers OpenRouter uses to attribute traffic to a project.
    #: Without them a provider account shows anonymous requests, which cannot be
    #: reconciled against the per-item token usage this service records.
    OPENROUTER_SITE_URL: str = "https://bidprice-estimator.local"
    OPENROUTER_APP_NAME: str = "BoQ Price Estimator"

    # ML Models
    ML_MODEL_PATH: str = "./model_artifacts"
    FORECAST_MODEL_PATH: str = "./model_artifacts/forecast"
    WEIGHT_FUSION_ALPHA: float = 0.7  # ML weight in fusion

    # ETL
    ETL_SCHEDULE_CRON: str = "0 2 * * *"  # 02:00 Tehran time
    ETL_TIMEZONE: str = "Asia/Tehran"
    MARKET_DATA_STALENESS_HOURS: int = 24
    #: How many times one source's refresh is retried before it is recorded as
    #: failed. A source that is still failing after this is left alone until the
    #: next run rather than retried in a tight loop.
    ETL_MAX_ATTEMPTS: int = 3
    #: Base for the ETL retry backoff, in seconds.
    ETL_RETRY_BASE_SECONDS: float = 2.0

    # Forecast
    FORECAST_HORIZONS: List[int] = [1, 3, 6, 12]
    FORECAST_SCENARIOS: List[str] = ["optimistic", "base", "pessimistic"]
    FORECAST_CONFIDENCE_LEVEL: float = 0.8
    MODEL_RETRAIN_SCHEDULE_CRON: str = "0 3 * * 0"  # Weekly Sunday 03:00
    #: Skip the weekly retrain when the newest market data is this stale. A
    #: retrain on stale inputs replaces a good model with a worse one and then
    #: reports the new one as active.
    RETRAIN_SKIP_IF_STALE_HOURS: int = 72

    # Commercial Adjustments (defaults, can be overridden per project)
    DEFAULT_RISK_BUFFER: float = 1.04
    DEFAULT_PAYMENT_TERMS: float = 1.08
    DEFAULT_PROFIT_MARGIN: float = 1.10

    # Export
    EXPORT_STORAGE_PATH: str = "./exports"
    PDF_FONT_PATH: str = "./fonts/Vazirmatn-Regular.ttf"

    # CORS
    ALLOWED_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:8000"]

    # Rate Limiting
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    #: 0 disables the limiter outright, rather than refusing everything.
    RATE_LIMIT_ENABLED: bool = True

    # Observability
    #: Whether ``/metrics`` is served. In production a scrape endpoint is public
    #: information about the deployment, so it is off unless asked for.
    METRICS_ENABLED: bool = True

    # File Upload
    MAX_FILE_SIZE_MB: int = 50
    ALLOWED_EXTENSIONS: List[str] = [".xlsx", ".xls"]

    @field_validator("ALLOWED_ORIGINS", "ALLOWED_HOSTS", "ALLOWED_EXTENSIONS",
                     "FORECAST_HORIZONS", "FORECAST_SCENARIOS", mode="before")
    @classmethod
    def _split_csv(cls, value):
        """
        Accept a comma-separated string as well as a list.

        Every one of these is set from an environment variable, where it always
        arrives as a string. Without this, ``ALLOWED_ORIGINS=a,b`` is a
        one-element list containing ``"a,b"``, which matches no origin at all -
        and the symptom is every browser request failing CORS with an origin
        that looks correctly configured in the environment file.
        """
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.strip().lower() in {"production", "prod"}

    def production_problems(self) -> List[str]:
        """
        Configuration that is unsafe to run in production, as sentences.

        Reported rather than raised. Refusing to boot turns a missing
        environment variable into an outage, and the operator learns less from a
        stack trace than from a list of what to fix - so the application logs
        these at startup and the deployment can also assert on them in CI.
        """
        if not self.is_production:
            return []

        problems: List[str] = []

        if self.JWT_SECRET_KEY == DEV_JWT_SECRET or not self.JWT_SECRET_KEY:
            problems.append(
                "JWT_SECRET_KEY is still the development default; every token "
                "in the deployment is forgeable by anyone who has read the "
                "source."
            )
        elif len(self.JWT_SECRET_KEY) < 32:
            problems.append(
                f"JWT_SECRET_KEY is {len(self.JWT_SECRET_KEY)} characters; "
                f"use at least 32."
            )

        if self.DEBUG:
            problems.append(
                "DEBUG is on in production: SQLAlchemy echoes every statement, "
                "including bound parameters."
            )

        if not self.FORCE_HTTPS:
            problems.append(
                "FORCE_HTTPS is off, so Strict-Transport-Security is not sent "
                "and a browser will not refuse a plaintext connection."
            )

        if not self.ALLOWED_HOSTS:
            problems.append(
                "ALLOWED_HOSTS is empty, so any Host header is accepted and "
                "the service answers to names it does not own - which is what "
                "a cache-poisoning or password-reset-link attack needs."
            )

        if "*" in self.ALLOWED_ORIGINS:
            problems.append(
                "ALLOWED_ORIGINS contains '*', which cannot be combined with "
                "credentials; every authenticated cross-origin request will "
                "fail in the browser."
            )

        if not self.OPENROUTER_API_KEY:
            problems.append(
                "OPENROUTER_API_KEY is empty. Weight attribution will run, but "
                "every item falls back to keyword matching and reports a 0.3 "
                "confidence that looks like an answer rather than a guess."
            )

        if self.METRICS_ENABLED:
            problems.append(
                "METRICS_ENABLED is on. /metrics discloses route names, error "
                "rates and the deployment's shape to anything that can reach it."
            )

        return problems

    def undocumented_settings(self) -> List[str]:
        """
        Settings present in the class but absent from ``.env.example``.

        An omission nobody notices until they try to configure that variable in
        a new environment and find the template has never heard of it.
        """
        if not ENV_TEMPLATE.exists():
            return []
        template = ENV_TEMPLATE.read_text(encoding="utf-8")
        declared = {
            line.split("=", 1)[0].strip()
            for line in template.splitlines()
            if "=" in line and not line.strip().startswith("#")
        }
        return sorted(
            name
            for name in type(self).model_fields
            if name.isupper() and name not in declared
        )


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
