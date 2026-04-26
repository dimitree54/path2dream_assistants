from __future__ import annotations

import json
from pathlib import Path

import pytest

from assistant_api.container_builder.container_plugin.openai_provider_login_plugin._opencode_config import (
    OpenCodeConfigError,
    configure_default_model,
)


def test_configure_default_model_creates_opencode_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    config_path = configure_default_model("openai/gpt-5.5")

    assert config_path == tmp_path / "config" / "opencode" / "opencode.json"
    assert (
        json.loads(config_path.read_text(encoding="utf-8"))["model"]
        == "openai/gpt-5.5"
    )


def test_configure_default_model_preserves_existing_config_keys(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    config_path = tmp_path / "config" / "opencode" / "opencode.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"username": "contract-user", "model": "openai/gpt-5.4"}),
        encoding="utf-8",
    )

    configure_default_model("openai/gpt-5.5-fast")

    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["username"] == "contract-user"
    assert config["model"] == "openai/gpt-5.5-fast"


def test_configure_default_model_fails_fast_on_invalid_existing_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    config_path = tmp_path / "config" / "opencode" / "opencode.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("not-json", encoding="utf-8")

    with pytest.raises(OpenCodeConfigError, match="invalid JSON"):
        configure_default_model("openai/gpt-5.5")
