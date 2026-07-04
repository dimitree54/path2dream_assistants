from __future__ import annotations

import inspect
from pathlib import PurePosixPath

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.container_builder.container_plugin import OPENCODE_RUNTIME_STATE_KEY
from assistant_api.models import ContainerRuntimeContext, OpenCodeRuntimeMetadata
from openai_provider_api_token_contract_helpers import (
    TOKEN_ENV_VAR,
    TOKEN_VALUE,
    OpenCodeRuntimeStatePlugin,
    RecordingContainer,
    service_class,
    unused_port,
)


def test_public_service_import_and_init_signature() -> None:
    service = service_class()
    signature = inspect.signature(service)

    assert service.__name__ == "OpenAIProviderApiTokenPluginService"
    assert list(signature.parameters) == [
        "api_token_env_var",
        "opencode_model",
        "replace_existing",
    ]
    assert signature.parameters["api_token_env_var"].default == "OPENAI_API_KEY"
    assert signature.parameters["opencode_model"].default == "openai/gpt-5.5"
    assert signature.parameters["replace_existing"].default is False


def test_init_does_not_require_api_token_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(TOKEN_ENV_VAR, raising=False)

    plugin = service_class()()

    assert plugin.api_token_env_var == TOKEN_ENV_VAR
    assert plugin.opencode_model == "openai/gpt-5.5"
    assert plugin.replace_existing is False


@pytest.mark.parametrize(
    "api_token_env_var",
    ["", "  ", "OPENAI API KEY", "1OPENAI_API_KEY", "OPENAI-API-KEY", 123],
)
def test_init_rejects_invalid_api_token_env_var(api_token_env_var: object) -> None:
    with pytest.raises(ConfigurationError, match="api_token_env_var"):
        service_class()(api_token_env_var=api_token_env_var)


@pytest.mark.parametrize(
    "opencode_model",
    ["", "   ", "gpt-5.5", "anthropic/claude-sonnet-4-5", "openai/", 123],
)
def test_init_rejects_invalid_opencode_model(opencode_model: object) -> None:
    with pytest.raises(ConfigurationError, match="opencode_model"):
        service_class()(opencode_model=opencode_model)


def test_init_rejects_non_boolean_replace_existing() -> None:
    with pytest.raises(ConfigurationError, match="replace_existing"):
        service_class()(replace_existing="yes")


def test_configure_container_requires_token_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(TOKEN_ENV_VAR, raising=False)

    with pytest.raises(ConfigurationError, match=TOKEN_ENV_VAR):
        ContainerBuilderService(plugins=[service_class()()])._prepare_specs()


def test_configure_container_rejects_empty_token_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_ENV_VAR, "")

    with pytest.raises(ConfigurationError, match=TOKEN_ENV_VAR):
        ContainerBuilderService(plugins=[service_class()()])._prepare_specs()


def test_configure_container_rejects_surrounding_token_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_ENV_VAR, " sk-contract-openai-token ")

    with pytest.raises(ConfigurationError, match="surrounding whitespace"):
        ContainerBuilderService(plugins=[service_class()()])._prepare_specs()


def test_prepare_specs_installs_helper_and_startup_task_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN_VALUE)
    plugin = service_class()(opencode_model="openai/gpt-5.5-fast")

    image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()

    assert "python3" in image_spec.apk_packages
    assert container_spec.env[TOKEN_ENV_VAR] == TOKEN_VALUE
    assert len(container_spec.startup_tasks) == 1
    task = container_spec.startup_tasks[0]
    assert task.name == "openai-api-token-auth"
    assert task.owner_plugin_name == "openai-provider-api-token"
    assert task.command[:3] == [
        "python3",
        "/opt/notes-assistant-api/openai_provider_api_token_auth.py",
        "install",
    ]
    assert "--api-token-env-var" in task.command
    assert "--opencode-model" in task.command
    assert "openai/gpt-5.5-fast" in task.command
    assert TOKEN_VALUE not in repr(task.command)
    assert TOKEN_VALUE not in "\n".join(image_spec.run_commands)


def test_prepare_specs_uses_custom_token_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CUSTOM_OPENAI_TOKEN", TOKEN_VALUE)

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[service_class()(api_token_env_var="CUSTOM_OPENAI_TOKEN")]
    )._prepare_specs()

    assert container_spec.env["CUSTOM_OPENAI_TOKEN"] == TOKEN_VALUE
    assert "CUSTOM_OPENAI_TOKEN" in container_spec.startup_tasks[0].command
    assert TOKEN_ENV_VAR not in container_spec.env


def test_prepare_specs_adds_replace_existing_flag_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN_VALUE)

    _image_spec, default_container = ContainerBuilderService(
        plugins=[service_class()()]
    )._prepare_specs()
    _image_spec, replace_container = ContainerBuilderService(
        plugins=[service_class()(replace_existing=True)]
    )._prepare_specs()

    assert "--replace-existing" not in default_container.startup_tasks[0].command
    assert "--replace-existing" in replace_container.startup_tasks[0].command


def test_post_start_validates_provider_when_opencode_runtime_metadata_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN_VALUE)
    plugin = service_class()()
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[
            OpenCodeRuntimeStatePlugin(api_container_port=unused_port()),
            plugin,
        ]
    )._prepare_specs()
    container = RecordingContainer()

    plugin.post_start(
        ContainerRuntimeContext(
            docker_client=object(),
            container=container,
            state=container_spec.state,
        )
    )

    assert container.commands == [
        [
            "python3",
            "/opt/notes-assistant-api/openai_provider_api_token_auth.py",
            "health",
            "--api-token-env-var",
            TOKEN_ENV_VAR,
            "--opencode-model",
            "openai/gpt-5.5",
            "--opencode-api-port",
            str(container_spec.state[OPENCODE_RUNTIME_STATE_KEY].api_container_port),
        ]
    ]
    assert TOKEN_VALUE not in repr(container.commands)


def test_post_start_validates_auth_file_without_opencode_runtime_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN_VALUE)
    plugin = service_class()()
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()
    container = RecordingContainer()

    plugin.post_start(
        ContainerRuntimeContext(
            docker_client=object(),
            container=container,
            state=container_spec.state,
        )
    )

    assert "--opencode-api-port" not in container.commands[0]


def test_post_start_fails_when_container_health_check_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN_VALUE)
    plugin = service_class()()
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()
    container = RecordingContainer(exit_code=1, output=b"health failed without token")

    with pytest.raises(RuntimeError, match="OpenAI API-token auth health check failed"):
        plugin.post_start(
            ContainerRuntimeContext(
                docker_client=object(),
                container=container,
                state=container_spec.state,
            )
        )


def test_post_start_uses_opencode_runtime_metadata_model_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN_VALUE)
    plugin = service_class()()
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()
    container_spec.state[OPENCODE_RUNTIME_STATE_KEY] = OpenCodeRuntimeMetadata(
        working_dir=PurePosixPath("/workspace"),
        api_container_port=4096,
    )
    container = RecordingContainer()

    plugin.post_start(
        ContainerRuntimeContext(
            docker_client=object(),
            container=container,
            state=container_spec.state,
        )
    )

    assert "--opencode-api-port" in container.commands[0]


def test_service_does_not_mutate_host_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(TOKEN_ENV_VAR, TOKEN_VALUE)

    ContainerBuilderService(plugins=[service_class()()])._prepare_specs()

    assert __import__("os").environ[TOKEN_ENV_VAR] == TOKEN_VALUE
    assert "OPENCODE_MODEL" not in __import__("os").environ
