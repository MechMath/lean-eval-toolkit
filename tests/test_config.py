from pathlib import Path

import pytest
from pydantic import SecretStr

from lean_eval_toolkit.config import Settings


def test_settings_can_be_populated_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_NAME", "local/model")
    monkeypatch.setenv("MODEL_API_KEY", "secret")
    monkeypatch.setenv("EVAL_RESULTS_DIR", "artifacts")

    settings = Settings(_env_file=None)

    assert settings.require_model_name() == "local/model"
    assert settings.model_api_key == SecretStr("secret")
    assert settings.eval_results_dir == Path("artifacts")


def test_model_name_is_only_required_when_model_is_used() -> None:
    settings = Settings(_env_file=None, model_name=None)

    with pytest.raises(ValueError, match="MODEL_NAME"):
        settings.require_model_name()
