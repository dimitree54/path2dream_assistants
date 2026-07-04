from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from assistant_api.container_builder.container_plugin.openai_provider_api_token_plugin._api_token_auth import (
    OpenAIProviderApiTokenError,
    install_api_token_auth,
    validate_api_token_auth,
)
from openai_provider_api_token_contract_helpers import TOKEN_ENV_VAR, TOKEN_VALUE, read_json


def test_startup_task_writes_api_token_auth_and_default_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_home = tmp_path / "data"
    config_home = tmp_path / "config"
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN_VALUE)
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))

    install_api_token_auth(
        api_token_env_var=TOKEN_ENV_VAR,
        opencode_model="openai/gpt-5.5",
        replace_existing=False,
    )

    auth_path = data_home / "opencode" / "auth.json"
    config_path = config_home / "opencode" / "opencode.json"
    assert read_json(auth_path)["openai"] == {"type": "api", "key": TOKEN_VALUE}
    assert read_json(config_path)["model"] == "openai/gpt-5.5"
    assert stat.S_IMODE(auth_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


def test_startup_task_preserves_unrelated_provider_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_home = tmp_path / "data"
    auth_path = data_home / "opencode" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text(
        json.dumps({"github": {"type": "api", "key": "gh-contract-token"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN_VALUE)
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    install_api_token_auth(
        api_token_env_var=TOKEN_ENV_VAR,
        opencode_model="openai/gpt-5.5",
        replace_existing=False,
    )

    auth = read_json(auth_path)
    assert auth["github"] == {"type": "api", "key": "gh-contract-token"}
    assert auth["openai"] == {"type": "api", "key": TOKEN_VALUE}


def test_startup_task_rejects_invalid_existing_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_path = tmp_path / "data" / "opencode" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text("{invalid", encoding="utf-8")
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN_VALUE)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    with pytest.raises(OpenAIProviderApiTokenError, match="invalid JSON"):
        install_api_token_auth(
            api_token_env_var=TOKEN_ENV_VAR,
            opencode_model="openai/gpt-5.5",
            replace_existing=False,
        )


def test_startup_task_rejects_non_object_auth_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_path = tmp_path / "data" / "opencode" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text("[]", encoding="utf-8")
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN_VALUE)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    with pytest.raises(OpenAIProviderApiTokenError, match="JSON object"):
        install_api_token_auth(
            api_token_env_var=TOKEN_ENV_VAR,
            opencode_model="openai/gpt-5.5",
            replace_existing=False,
        )


def test_startup_task_rejects_existing_oauth_when_replace_existing_is_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_path = tmp_path / "data" / "opencode" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text(
        json.dumps({"openai": {"type": "oauth", "refresh": "refresh-token"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN_VALUE)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    with pytest.raises(OpenAIProviderApiTokenError, match="conflicts"):
        install_api_token_auth(
            api_token_env_var=TOKEN_ENV_VAR,
            opencode_model="openai/gpt-5.5",
            replace_existing=False,
        )


def test_startup_task_replaces_existing_openai_auth_only_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_path = tmp_path / "data" / "opencode" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text(
        json.dumps({"openai": {"type": "api", "key": "old-token"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN_VALUE)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    install_api_token_auth(
        api_token_env_var=TOKEN_ENV_VAR,
        opencode_model="openai/gpt-5.5",
        replace_existing=True,
    )

    assert read_json(auth_path)["openai"] == {"type": "api", "key": TOKEN_VALUE}


def test_startup_task_preserves_matching_existing_openai_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_path = tmp_path / "data" / "opencode" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text(
        json.dumps({"openai": {"type": "api", "key": TOKEN_VALUE}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN_VALUE)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    install_api_token_auth(
        api_token_env_var=TOKEN_ENV_VAR,
        opencode_model="openai/gpt-5.5",
        replace_existing=False,
    )

    assert read_json(auth_path)["openai"] == {"type": "api", "key": TOKEN_VALUE}


def test_startup_task_writes_through_persistence_auth_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_home = tmp_path / "data"
    auth_path = data_home / "opencode" / "auth.json"
    persisted_auth_path = tmp_path / "persisted-auth" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    persisted_auth_path.parent.mkdir(parents=True)
    auth_path.symlink_to(persisted_auth_path)
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN_VALUE)
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    install_api_token_auth(
        api_token_env_var=TOKEN_ENV_VAR,
        opencode_model="openai/gpt-5.5",
        replace_existing=False,
    )

    assert auth_path.is_symlink()
    assert read_json(persisted_auth_path)["openai"] == {"type": "api", "key": TOKEN_VALUE}


def test_health_check_succeeds_only_for_matching_api_key_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_path = tmp_path / "data" / "opencode" / "auth.json"
    config_path = tmp_path / "config" / "opencode" / "opencode.json"
    auth_path.parent.mkdir(parents=True)
    config_path.parent.mkdir(parents=True)
    auth_path.write_text(
        json.dumps({"openai": {"type": "api", "key": TOKEN_VALUE}}),
        encoding="utf-8",
    )
    config_path.write_text(json.dumps({"model": "openai/gpt-5.5"}), encoding="utf-8")
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN_VALUE)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    validate_api_token_auth(
        api_token_env_var=TOKEN_ENV_VAR,
        opencode_model="openai/gpt-5.5",
        opencode_api_port=None,
    )


@pytest.mark.parametrize(
    ("auth", "expected_message"),
    [
        ({}, "missing"),
        ({"openai": {"type": "oauth", "refresh": "refresh-token"}}, "not API-token"),
        ({"openai": {"type": "api", "key": "different-token"}}, "does not match"),
    ],
)
def test_health_check_rejects_invalid_auth_state(
    auth: dict[str, object],
    expected_message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_path = tmp_path / "data" / "opencode" / "auth.json"
    config_path = tmp_path / "config" / "opencode" / "opencode.json"
    auth_path.parent.mkdir(parents=True)
    config_path.parent.mkdir(parents=True)
    auth_path.write_text(json.dumps(auth), encoding="utf-8")
    config_path.write_text(json.dumps({"model": "openai/gpt-5.5"}), encoding="utf-8")
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN_VALUE)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    with pytest.raises(OpenAIProviderApiTokenError, match=expected_message):
        validate_api_token_auth(
            api_token_env_var=TOKEN_ENV_VAR,
            opencode_model="openai/gpt-5.5",
            opencode_api_port=None,
        )


def test_health_check_rejects_unconfigured_default_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_path = tmp_path / "data" / "opencode" / "auth.json"
    config_path = tmp_path / "config" / "opencode" / "opencode.json"
    auth_path.parent.mkdir(parents=True)
    config_path.parent.mkdir(parents=True)
    auth_path.write_text(
        json.dumps({"openai": {"type": "api", "key": TOKEN_VALUE}}),
        encoding="utf-8",
    )
    config_path.write_text(json.dumps({"model": "openai/other"}), encoding="utf-8")
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN_VALUE)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    with pytest.raises(OpenAIProviderApiTokenError, match="default model"):
        validate_api_token_auth(
            api_token_env_var=TOKEN_ENV_VAR,
            opencode_model="openai/gpt-5.5",
            opencode_api_port=None,
        )


def test_startup_task_uses_home_paths_without_xdg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN_VALUE)

    install_api_token_auth(
        api_token_env_var=TOKEN_ENV_VAR,
        opencode_model="openai/gpt-5.5",
        replace_existing=False,
    )

    assert read_json(home / ".local" / "share" / "opencode" / "auth.json")["openai"] == {
        "type": "api",
        "key": TOKEN_VALUE,
    }
    assert read_json(home / ".config" / "opencode" / "opencode.json")["model"] == (
        "openai/gpt-5.5"
    )
