from pathlib import Path

import pytest
from pydantic import SecretStr

from lean_eval_toolkit.config import DEFAULT_CONFIG_PATH, load_dataset_settings, load_settings


def test_settings_can_be_populated_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_NAME", "local/model")
    monkeypatch.setenv("MODEL_API_KEY", "secret")
    monkeypatch.setenv("MODEL_MAX_RETRIES", "4")
    monkeypatch.setenv("AXLE_MAX_RETRIES", "5")
    monkeypatch.setenv("RETRY_BACKOFF_SECONDS", "0.25")
    monkeypatch.setenv("EVAL_RESULTS_DIR", "artifacts")

    settings = load_settings(env_file=None)

    assert settings.require_model_name() == "local/model"
    assert settings.model.api_key == SecretStr("secret")
    assert settings.model.max_retries == 4
    assert settings.axle.max_retries == 5
    assert settings.retry.backoff_seconds == 0.25
    assert settings.evaluation.results_dir == Path("artifacts")
    assert settings.evaluation.test_template == "lean-cot-v1"
    assert settings.evaluation.max_truncation_retries == 0


def test_wuprover_enables_one_fresh_retry_after_truncation() -> None:
    config = DEFAULT_CONFIG_PATH.with_name("wuprover.yaml")
    settings = load_settings(config_path=config, env_file=None)

    assert settings.evaluation.max_truncation_retries == 1


def test_model_name_is_only_required_when_model_is_used() -> None:
    settings = load_settings(env_file=None, overrides={"model": {"name": None}})

    with pytest.raises(ValueError, match="MODEL_NAME"):
        settings.require_model_name()


def test_process_environment_overrides_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MODEL_NAME=dotenv-model\nMODEL_API_KEY=dotenv-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MODEL_NAME", "process-model")
    monkeypatch.delenv("MODEL_API_KEY", raising=False)

    settings = load_settings(env_file=env_file)

    assert settings.model.name == "process-model"
    assert settings.model.api_key == SecretStr("dotenv-secret")


def test_yaml_config_imports_independent_dataset_registry(tmp_path: Path) -> None:
    datasets = tmp_path / "datasets.yaml"
    datasets.write_text(
        """datasets:
  custom:
    description: Custom benchmark
    upstream: https://example.com/custom
    layout: JSONL
    fallback_environment: lean-4.30.0
""",
        encoding="utf-8",
    )
    config = tmp_path / "config.yaml"
    config.write_text(
        f"includes:\n  - {DEFAULT_CONFIG_PATH}\n  - datasets.yaml\n",
        encoding="utf-8",
    )

    settings = load_settings(config_path=config, env_file=None)
    registry = load_dataset_settings(config)

    assert settings.model.base_url == "http://localhost:8000/v1"
    assert settings.datasets["custom"].fallback_environment == "lean-4.30.0"
    assert registry["custom"].description == "Custom benchmark"
