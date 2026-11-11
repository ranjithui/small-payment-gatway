"""Runtime configuration (environment driven)."""
import os
from dataclasses import dataclass, field


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def normalize_database_url(url: str) -> str:
    """Hosting providers hand out ``postgres://`` / ``postgresql://`` URLs; use the psycopg 3 driver."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _database_url() -> str:
    return normalize_database_url(
        os.getenv("FEEFLOW_DATABASE_URL") or os.getenv("DATABASE_URL") or "sqlite:///./feeflow.db"
    )


def _public_base_url() -> str:
    # Render sets RENDER_EXTERNAL_URL (https://<service>.onrender.com) automatically.
    url = os.getenv("FEEFLOW_PUBLIC_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or "http://localhost:8000"
    return url.rstrip("/")


DEV_SECRET = "dev-insecure-secret-change-me"


@dataclass
class Settings:
    environment: str = field(default_factory=lambda: os.getenv("FEEFLOW_ENV", "development").lower())
    database_url: str = field(default_factory=_database_url)
    secret_key: str = field(default_factory=lambda: os.getenv("FEEFLOW_SECRET_KEY", DEV_SECRET))
    public_base_url: str = field(default_factory=_public_base_url)
    admin_token_ttl_seconds: int = field(default_factory=lambda: int(os.getenv("FEEFLOW_ADMIN_TOKEN_TTL", "28800")))
    rate_limit_enabled: bool = field(default_factory=lambda: _bool("FEEFLOW_RATE_LIMIT", "1"))
    background_jobs: bool = field(default_factory=lambda: _bool("FEEFLOW_BACKGROUND_JOBS", "1"))
    background_interval_seconds: int = field(default_factory=lambda: int(os.getenv("FEEFLOW_JOB_INTERVAL", "60")))
    # Load the demo centre/students on first start (trial deployments only).
    seed_demo: bool = field(default_factory=lambda: _bool("FEEFLOW_SEED_DEMO", "0"))

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def validate(self) -> None:
        if not self.is_production:
            return
        problems = []
        if self.secret_key == DEV_SECRET or len(self.secret_key) < 32:
            problems.append("FEEFLOW_SECRET_KEY must be set to a random value of at least 32 characters")
        if self.database_url.startswith("sqlite"):
            problems.append("Use PostgreSQL in production (set DATABASE_URL / FEEFLOW_DATABASE_URL)")
        if not self.public_base_url.startswith("https://"):
            problems.append("FEEFLOW_PUBLIC_BASE_URL must be an https:// URL in production")
        if problems:
            raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))


settings = Settings()
