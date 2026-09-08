"""Environment-backed application configuration."""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables and ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    model_base_url: str = "http://localhost:8000/v1"
    model_api_key: SecretStr = SecretStr("")
    model_name: str | None = None
    model_timeout_seconds: float = Field(default=300, gt=0)
    model_max_tokens: int = Field(default=4096, gt=0)
    model_temperature: float = Field(default=0, ge=0)
    model_extra_body: dict[str, object] = Field(default_factory=dict)
    model_extra_headers: dict[str, str] = Field(default_factory=dict)

    axle_api_url: str = "https://axle.axiommath.ai"
    axle_api_key: SecretStr = SecretStr("")
    axle_environment: str = "lean-4.32.0"
    axle_timeout_seconds: float = Field(default=900, gt=0, le=900)

    eval_concurrency: int = Field(default=4, gt=0)
    eval_attempts: int = Field(default=1, gt=0)
    eval_results_dir: Path = Path("results")

    def require_model_name(self) -> str:
        """Return the configured model name or explain how to provide it."""
        if not self.model_name:
            raise ValueError("MODEL_NAME is required; copy .env.example to .env and set it")
        return self.model_name
