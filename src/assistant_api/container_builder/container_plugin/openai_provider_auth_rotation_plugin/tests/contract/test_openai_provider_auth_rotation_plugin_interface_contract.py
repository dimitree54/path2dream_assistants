from __future__ import annotations

import inspect
import json
from pathlib import PurePosixPath

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder._errors import ConfigurationError
from openai_provider_auth_rotation_contract_helpers import (
    TOKEN_ENV_VAR,
    TOKEN_VALUE,
    OpenCodeRuntimeStatePlugin,
    api_auth,
    service_class,
    unused_port,
    write_auth_file,
)


def test_public_service_import_and_init_signature(tmp_path) -> None:
    auth_file = write_auth_file(tmp_path / "auth.json", api_auth())
    service = service_class()
    signature = inspect.signature(service)

    assert service.__name__ == "OpenAIProviderAuthRotationPluginService"
    assert list(signature.parameters) == [
        "candidate_auth_files",
        "fallback_api_token_env_var",
        "opencode_model",
        "probe_model",
        "probe_variant",
        "probe_message",
        "probe_expected_text",
        "probe_timeout_seconds",
        "on_auth_alert",
    ]
    plugin = service([auth_file])
    assert plugin.fallback_api_token_env_var == "OPENAI_API_KEY"
    assert plugin.opencode_model == "openai/gpt-5.5"
    assert plugin.probe_model == "openai/gpt-5.4-mini"
    assert plugin.probe_variant == "low"
    assert plugin.probe_message == "hi"
    assert plugin.probe_expected_text is None
    assert plugin.probe_timeout_seconds == 180


@pytest.mark.parametrize("candidate_auth_files", [[], (), "not-a-sequence"])
def test_init_rejects_empty_or_invalid_candidate_list(candidate_auth_files: object) -> None:
    with pytest.raises(ConfigurationError, match="candidate_auth_files"):
        service_class()(candidate_auth_files)


def test_init_rejects_missing_candidate_file(tmp_path) -> None:
    with pytest.raises(ConfigurationError, match="candidate auth file must exist"):
        service_class()([tmp_path / "missing-auth.json"])


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("{invalid", "invalid JSON"),
        ("[]", "JSON object"),
        (json.dumps({"github": {"type": "api", "key": "gh"}}), "openai auth"),
        (json.dumps({"openai": {"type": "api"}}), "API auth"),
        (json.dumps({"openai": {"type": "oauth", "refresh": "r"}}), "OAuth auth"),
    ],
)
def test_init_rejects_malformed_candidate_files(
    content: str,
    expected: str,
    tmp_path,
) -> None:
    auth_file = tmp_path / "candidate.json"
    auth_file.write_text(content, encoding="utf-8")

    with pytest.raises(ConfigurationError, match=expected):
        service_class()([auth_file])


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"fallback_api_token_env_var": "OPENAI API KEY"}, "fallback_api_token_env_var"),
        ({"fallback_api_token_env_var": "1OPENAI_API_KEY"}, "fallback_api_token_env_var"),
        ({"opencode_model": "gpt-5.5"}, "opencode_model"),
        ({"probe_model": "gpt-5.4-mini"}, "probe_model"),
        ({"probe_variant": ""}, "probe_variant"),
        ({"probe_message": " hi "}, "probe_message"),
        ({"probe_expected_text": ""}, "probe_expected_text"),
        ({"probe_timeout_seconds": 0}, "probe_timeout_seconds"),
        ({"probe_timeout_seconds": "180"}, "probe_timeout_seconds"),
    ],
)
def test_init_rejects_invalid_configuration(kwargs: dict[str, object], expected: str, tmp_path) -> None:
    auth_file = write_auth_file(tmp_path / "auth.json", api_auth())

    with pytest.raises(ConfigurationError, match=expected):
        service_class()([auth_file], **kwargs)


def test_configure_container_requires_fallback_token_env_var(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_file = write_auth_file(tmp_path / "auth.json", api_auth())
    monkeypatch.delenv(TOKEN_ENV_VAR, raising=False)

    with pytest.raises(ConfigurationError, match=TOKEN_ENV_VAR):
        ContainerBuilderService(plugins=[service_class()([auth_file])])._prepare_specs()


def test_configure_container_rejects_surrounding_token_whitespace(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_file = write_auth_file(tmp_path / "auth.json", api_auth())
    monkeypatch.setenv(TOKEN_ENV_VAR, " sk-contract ")

    with pytest.raises(ConfigurationError, match="surrounding whitespace"):
        ContainerBuilderService(plugins=[service_class()([auth_file])])._prepare_specs()


def test_prepare_specs_mounts_candidates_readonly_and_does_not_leak_auth_content(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sk-contract-candidate-secret"
    first = write_auth_file(tmp_path / "first.json", api_auth(secret, source="first"))
    second = write_auth_file(tmp_path / "second.json", api_auth("sk-second", source="second"))
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN_VALUE)
    plugin = service_class()(
        [first, second],
        opencode_model="openai/gpt-5.5-fast",
        probe_model="openai/gpt-5.4-mini",
        probe_variant="low",
        probe_message="hi",
        probe_expected_text="hello",
        probe_timeout_seconds=11,
    )

    image_spec, container_spec = ContainerBuilderService(
        plugins=[OpenCodeRuntimeStatePlugin(unused_port()), plugin]
    )._prepare_specs()

    assert "python3" in image_spec.apk_packages
    assert container_spec.env[TOKEN_ENV_VAR] == TOKEN_VALUE
    assert len(container_spec.startup_tasks) == 1
    task = container_spec.startup_tasks[0]
    assert task.name == "openai-auth-rotation"
    assert task.owner_plugin_name == "openai-provider-auth-rotation"
    assert task.command[:2] == [
        "python3",
        "/opt/notes-assistant-api/openai_provider_auth_rotation.py",
    ]
    assert task.command.count("--candidate-auth-file") == 2
    assert "--fallback-api-token-env-var" in task.command
    assert "--opencode-model" in task.command
    assert "openai/gpt-5.5-fast" in task.command
    assert "--probe-model" in task.command
    assert "openai/gpt-5.4-mini" in task.command
    assert "--probe-variant" in task.command
    assert "low" in task.command
    assert "--probe-message" in task.command
    assert "hi" in task.command
    assert "--probe-expected-text" in task.command
    assert "hello" in task.command
    assert "--probe-timeout-seconds" in task.command
    assert "11" in task.command
    assert "--working-dir" in task.command
    assert "/workspace" in task.command

    mounts = list(container_spec.volumes.values())
    assert len(mounts) == 2
    assert {mount.mode for mount in mounts} == {"ro"}
    assert {mount.type for mount in mounts} == {"bind"}
    assert all(str(mount.target).startswith("/tmp/notes-assistant/openai-auth-rotation") for mount in mounts)
    assert secret not in repr(task.command)
    assert secret not in "\n".join(image_spec.run_commands)
    assert json.dumps({"source": "first"}) not in repr(task.command)


def test_prepare_specs_uses_container_working_dir_without_opencode_runtime_state(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_file = write_auth_file(tmp_path / "auth.json", api_auth())
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN_VALUE)

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[service_class()([auth_file])]
    )._prepare_specs()

    command = container_spec.startup_tasks[0].command
    assert command[command.index("--working-dir") + 1] == "/workspace"


def test_prepare_specs_uses_custom_working_dir_from_existing_container_spec(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_file = write_auth_file(tmp_path / "auth.json", api_auth())
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN_VALUE)
    plugin = service_class()([auth_file])

    from assistant_api.models import ContainerSpec

    container = ContainerSpec(name="contract", image_tag="contract:latest")
    container.working_dir = PurePosixPath("/custom-workspace")
    plugin.configure_container(container)

    command = container.startup_tasks[0].command
    assert command[command.index("--working-dir") + 1] == "/custom-workspace"
