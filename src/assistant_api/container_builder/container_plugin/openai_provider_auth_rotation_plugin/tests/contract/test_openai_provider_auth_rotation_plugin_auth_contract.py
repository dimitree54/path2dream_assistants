from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from assistant_api.container_builder.container_plugin.openai_provider_auth_rotation_plugin import (
    _auth_rotation,
)
from assistant_api.container_builder.container_plugin.openai_provider_auth_rotation_plugin._auth_rotation import (
    OpenAIProviderAuthRotationError,
    install_rotated_auth,
)
from openai_provider_auth_rotation_contract_helpers import (
    TOKEN_ENV_VAR,
    TOKEN_VALUE,
    api_auth,
    read_json,
    write_auth_file,
)


def test_startup_selects_first_working_candidate_and_preserves_active_providers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = write_auth_file(
        tmp_path / "first.json",
        api_auth("bad-token", source="first"),
        extra={"github": {"type": "api", "key": "candidate-gh"}},
    )
    second = write_auth_file(tmp_path / "second.json", api_auth("good-token", source="second"))
    active_path = _prepare_active_auth(
        tmp_path,
        monkeypatch,
        {"github": {"type": "api", "key": "active-gh"}},
    )
    seen_keys: list[str] = []

    def fake_probe(**_kwargs: object) -> None:
        key = read_json(active_path)["openai"]["key"]
        seen_keys.append(key)
        if key == "bad-token":
            raise OpenAIProviderAuthRotationError("probe failed")

    monkeypatch.setattr(_auth_rotation, "_candidate_order", lambda _count: [0, 1])
    monkeypatch.setattr(_auth_rotation, "_probe_active_auth", fake_probe)

    install_rotated_auth(
        candidate_auth_files=[first, second],
        fallback_api_token_env_var=TOKEN_ENV_VAR,
        opencode_model="openai/gpt-5.5",
        probe_model="openai/gpt-5.4-mini",
        probe_variant="low",
        probe_message="hi",
        probe_expected_text=None,
        probe_timeout_seconds=180,
        working_dir=tmp_path / "workspace",
    )

    active = read_json(active_path)
    assert seen_keys == ["bad-token", "good-token"]
    assert active["openai"] == api_auth("good-token", source="second")
    assert active["github"] == {"type": "api", "key": "active-gh"}
    assert "candidate-gh" not in json.dumps(active)
    assert (
        _auth_rotation.AUTH_ROTATION_RESULT_PATH.read_text(encoding="utf-8").strip()
        == _auth_rotation.AUTH_ROTATION_RESULT_CANDIDATE
    )


def test_startup_uses_random_order_without_retrying_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = write_auth_file(tmp_path / "first.json", api_auth("first-token"))
    second = write_auth_file(tmp_path / "second.json", api_auth("second-token"))
    active_path = _prepare_active_auth(tmp_path, monkeypatch, {})
    seen_keys: list[str] = []

    def fake_probe(**_kwargs: object) -> None:
        seen_keys.append(read_json(active_path)["openai"]["key"])

    monkeypatch.setattr(_auth_rotation, "_candidate_order", lambda _count: [1, 0])
    monkeypatch.setattr(_auth_rotation, "_probe_active_auth", fake_probe)

    install_rotated_auth(
        candidate_auth_files=[first, second],
        fallback_api_token_env_var=TOKEN_ENV_VAR,
        opencode_model="openai/gpt-5.5",
        probe_model="openai/gpt-5.4-mini",
        probe_variant="low",
        probe_message="hi",
        probe_expected_text=None,
        probe_timeout_seconds=180,
        working_dir=tmp_path / "workspace",
    )

    assert seen_keys == ["second-token"]
    assert read_json(active_path)["openai"]["key"] == "second-token"


def test_startup_falls_back_to_api_token_after_all_candidates_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = write_auth_file(tmp_path / "candidate.json", api_auth("bad-token"))
    active_path = _prepare_active_auth(tmp_path, monkeypatch, {})
    seen_keys: list[str] = []

    def fake_probe(**_kwargs: object) -> None:
        key = read_json(active_path)["openai"]["key"]
        seen_keys.append(key)
        if key == "bad-token":
            raise OpenAIProviderAuthRotationError("probe failed")

    monkeypatch.setattr(_auth_rotation, "_candidate_order", lambda _count: [0])
    monkeypatch.setattr(_auth_rotation, "_probe_active_auth", fake_probe)

    install_rotated_auth(
        candidate_auth_files=[candidate],
        fallback_api_token_env_var=TOKEN_ENV_VAR,
        opencode_model="openai/gpt-5.5",
        probe_model="openai/gpt-5.4-mini",
        probe_variant="low",
        probe_message="hi",
        probe_expected_text=None,
        probe_timeout_seconds=180,
        working_dir=tmp_path / "workspace",
    )

    assert seen_keys == ["bad-token", TOKEN_VALUE]
    assert read_json(active_path)["openai"] == {"type": "api", "key": TOKEN_VALUE}
    assert (
        _auth_rotation.AUTH_ROTATION_RESULT_PATH.read_text(encoding="utf-8").strip()
        == _auth_rotation.AUTH_ROTATION_RESULT_FALLBACK
    )


def test_startup_restores_original_active_auth_when_all_attempts_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = write_auth_file(tmp_path / "candidate.json", api_auth("bad-token"))
    original = {"openai": api_auth("original-token"), "github": {"type": "api", "key": "gh"}}
    active_path = _prepare_active_auth(tmp_path, monkeypatch, original)

    def fake_probe(**_kwargs: object) -> None:
        raise OpenAIProviderAuthRotationError("probe failed")

    monkeypatch.setattr(_auth_rotation, "_candidate_order", lambda _count: [0])
    monkeypatch.setattr(_auth_rotation, "_probe_active_auth", fake_probe)

    with pytest.raises(OpenAIProviderAuthRotationError, match="No OpenAI provider auth"):
        install_rotated_auth(
            candidate_auth_files=[candidate],
            fallback_api_token_env_var=TOKEN_ENV_VAR,
            opencode_model="openai/gpt-5.5",
            probe_model="openai/gpt-5.4-mini",
            probe_variant="low",
            probe_message="hi",
            probe_expected_text=None,
            probe_timeout_seconds=180,
            working_dir=tmp_path / "workspace",
        )

    assert read_json(active_path) == original


def test_startup_writes_through_persistence_auth_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = write_auth_file(tmp_path / "candidate.json", api_auth("good-token"))
    data_home = tmp_path / "data"
    auth_path = data_home / "opencode" / "auth.json"
    persisted_path = tmp_path / "persisted" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    persisted_path.parent.mkdir(parents=True)
    auth_path.symlink_to(persisted_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN_VALUE)
    monkeypatch.setattr(_auth_rotation, "_candidate_order", lambda _count: [0])
    monkeypatch.setattr(_auth_rotation, "_probe_active_auth", lambda **_kwargs: None)

    install_rotated_auth(
        candidate_auth_files=[candidate],
        fallback_api_token_env_var=TOKEN_ENV_VAR,
        opencode_model="openai/gpt-5.5",
        probe_model="openai/gpt-5.4-mini",
        probe_variant="low",
        probe_message="hi",
        probe_expected_text=None,
        probe_timeout_seconds=180,
        working_dir=tmp_path / "workspace",
    )

    assert auth_path.is_symlink()
    assert read_json(persisted_path)["openai"]["key"] == "good-token"
    assert stat.S_IMODE(persisted_path.stat().st_mode) == 0o600


def test_startup_rejects_invalid_active_auth_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = write_auth_file(tmp_path / "candidate.json", api_auth("good-token"))
    data_home = tmp_path / "data"
    active_path = data_home / "opencode" / "auth.json"
    active_path.parent.mkdir(parents=True)
    active_path.write_text("{invalid", encoding="utf-8")
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN_VALUE)

    with pytest.raises(OpenAIProviderAuthRotationError, match="invalid JSON"):
        install_rotated_auth(
            candidate_auth_files=[candidate],
            fallback_api_token_env_var=TOKEN_ENV_VAR,
            opencode_model="openai/gpt-5.5",
            probe_model="openai/gpt-5.4-mini",
            probe_variant="low",
            probe_message="hi",
            probe_expected_text=None,
            probe_timeout_seconds=180,
            working_dir=tmp_path / "workspace",
        )


def test_startup_accepts_oauth_candidate_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oauth = {
        "type": "oauth",
        "refresh": "refresh-token",
        "access": "access-token",
        "expires": 4_102_444_800_000,
    }
    candidate = write_auth_file(tmp_path / "candidate.json", oauth)
    active_path = _prepare_active_auth(tmp_path, monkeypatch, {})
    monkeypatch.setattr(_auth_rotation, "_candidate_order", lambda _count: [0])
    monkeypatch.setattr(_auth_rotation, "_probe_active_auth", lambda **_kwargs: None)

    install_rotated_auth(
        candidate_auth_files=[candidate],
        fallback_api_token_env_var=TOKEN_ENV_VAR,
        opencode_model="openai/gpt-5.5",
        probe_model="openai/gpt-5.4-mini",
        probe_variant="low",
        probe_message="hi",
        probe_expected_text=None,
        probe_timeout_seconds=180,
        working_dir=tmp_path / "workspace",
    )

    assert read_json(active_path)["openai"] == oauth


def _prepare_active_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> Path:
    data_home = tmp_path / "data"
    config_home = tmp_path / "config"
    active_path = data_home / "opencode" / "auth.json"
    active_path.parent.mkdir(parents=True, exist_ok=True)
    active_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN_VALUE)
    return active_path
