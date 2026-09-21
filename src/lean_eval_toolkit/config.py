"""Layered YAML, dotenv, and environment-backed application configuration."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from omegaconf import DictConfig, OmegaConf
from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter

DEFAULT_CONFIG_PATH = Path(__file__).with_name("conf") / "default.yaml"


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    base_url: str
    api_key: SecretStr = SecretStr("")
    name: str | None = None
    timeout_seconds: float = Field(gt=0)
    max_retries: int = Field(ge=0)
    max_tokens: int = Field(gt=0)
    temperature: float = Field(ge=0)
    # A tokenizer/provider chat template is passed through to the model server.
    # It is never used to render benchmark messages inside this toolkit.
    chat_template: str | None = None
    extra_body: dict[str, object] = Field(default_factory=dict)
    extra_headers: dict[str, str] = Field(default_factory=dict)


class AxleSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    api_url: str
    api_key: SecretStr = SecretStr("")
    environment: str | None = None
    timeout_seconds: float = Field(gt=0, le=900)
    max_retries: int = Field(ge=0)


class RetrySettings(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    backoff_seconds: float = Field(ge=0)


class EvaluationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    concurrency: int = Field(gt=0)
    attempts: int = Field(gt=0)
    results_dir: Path
    test_template: str = "lean-cot-v1"


class DatasetSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    upstream: str
    layout: str
    fallback_environment: str | None = None
    environments: dict[str, str] = Field(default_factory=dict)
    imports: dict[str, str] = Field(default_factory=dict)


class Settings(BaseModel):
    """Validated configuration assembled by :func:`load_settings`."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    model: ModelSettings
    axle: AxleSettings
    retry: RetrySettings
    evaluation: EvaluationSettings
    datasets: dict[str, DatasetSettings]

    def require_model_name(self) -> str:
        if not self.model.name:
            raise ValueError("MODEL_NAME is required; copy .env.example to .env and set it")
        return self.model.name


_ENV_PATHS = {
    "MODEL_BASE_URL": "model.base_url",
    "MODEL_API_KEY": "model.api_key",
    "MODEL_NAME": "model.name",
    "MODEL_TIMEOUT_SECONDS": "model.timeout_seconds",
    "MODEL_MAX_RETRIES": "model.max_retries",
    "MODEL_MAX_TOKENS": "model.max_tokens",
    "MODEL_TEMPERATURE": "model.temperature",
    "MODEL_CHAT_TEMPLATE": "model.chat_template",
    "MODEL_EXTRA_BODY": "model.extra_body",
    "MODEL_EXTRA_HEADERS": "model.extra_headers",
    "AXLE_API_URL": "axle.api_url",
    "AXLE_API_KEY": "axle.api_key",
    "AXLE_ENVIRONMENT": "axle.environment",
    "AXLE_TIMEOUT_SECONDS": "axle.timeout_seconds",
    "AXLE_MAX_RETRIES": "axle.max_retries",
    "RETRY_BACKOFF_SECONDS": "retry.backoff_seconds",
    "EVAL_CONCURRENCY": "evaluation.concurrency",
    "EVAL_ATTEMPTS": "evaluation.attempts",
    "EVAL_RESULTS_DIR": "evaluation.results_dir",
    "EVAL_TEST_TEMPLATE": "evaluation.test_template",
}
_JSON_ENV_VARS = {"MODEL_EXTRA_BODY", "MODEL_EXTRA_HEADERS"}
_OPTIONAL_ENV_VARS = {"MODEL_NAME", "MODEL_CHAT_TEMPLATE", "AXLE_ENVIRONMENT"}


def _load_yaml(path: Path, stack: tuple[Path, ...] = ()) -> DictConfig:
    path = path.resolve()
    if path in stack:
        chain = " -> ".join(str(item) for item in (*stack, path))
        raise ValueError(f"cyclic YAML config include: {chain}")
    if not path.is_file():
        raise ValueError(f"configuration file does not exist: {path}")
    current = OmegaConf.load(path)
    if not isinstance(current, DictConfig):
        raise ValueError(f"configuration root must be a mapping: {path}")
    includes = current.pop("includes", [])
    if not isinstance(includes, list) and not OmegaConf.is_list(includes):
        raise ValueError(f"`includes` must be a list in {path}")
    merged = OmegaConf.create({})
    for include in includes:
        included_path = path.parent / str(include)
        merged = OmegaConf.merge(merged, _load_yaml(included_path, (*stack, path)))
    return OmegaConf.merge(merged, current)


def _set_nested(target: dict[str, Any], dotted_path: str, value: Any) -> None:
    parts = dotted_path.split(".")
    node = target
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _environment_values(env_file: Path | str | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if env_file is not None:
        values.update(
            {key: value for key, value in dotenv_values(env_file).items() if value is not None}
        )
    values.update(os.environ)
    return values


def _environment_overrides(values: Mapping[str, str]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for variable, path in _ENV_PATHS.items():
        if variable not in values:
            continue
        value: Any = values[variable]
        if variable in _JSON_ENV_VARS:
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{variable} must contain a JSON object") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{variable} must contain a JSON object")
        elif variable in _OPTIONAL_ENV_VARS and value == "":
            value = None
        _set_nested(overrides, path, value)
    return overrides


def load_settings(
    *,
    config_path: Path | str | None = None,
    env_file: Path | str | None = ".env",
    overrides: Mapping[str, Any] | None = None,
) -> Settings:
    """Load included YAML files, then overlay dotenv, environment, and explicit values."""
    environment = _environment_values(env_file)
    selected_path = config_path or environment.get("LEAN_EVAL_CONFIG") or DEFAULT_CONFIG_PATH
    config = _load_yaml(Path(selected_path))
    config = OmegaConf.merge(config, OmegaConf.create(_environment_overrides(environment)))
    if overrides:
        config = OmegaConf.merge(config, OmegaConf.create(dict(overrides)))
    payload = OmegaConf.to_container(config, resolve=True)
    if not isinstance(payload, dict):
        raise ValueError("merged configuration root must be a mapping")
    return Settings.model_validate(payload)


def load_dataset_settings(
    config_path: Path | str | None = None,
    env_file: Path | str | None = ".env",
) -> dict[str, DatasetSettings]:
    """Load only the dataset registry from the composed YAML configuration."""
    environment = _environment_values(env_file)
    selected_path = config_path or environment.get("LEAN_EVAL_CONFIG") or DEFAULT_CONFIG_PATH
    config = _load_yaml(Path(selected_path))
    payload = OmegaConf.to_container(config.get("datasets"), resolve=True)
    return TypeAdapter(dict[str, DatasetSettings]).validate_python(payload)
