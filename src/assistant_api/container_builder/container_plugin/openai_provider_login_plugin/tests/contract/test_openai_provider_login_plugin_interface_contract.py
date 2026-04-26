from __future__ import annotations

import inspect

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.models import ContainerSpec
from openai_provider_login_contract_helpers import REQUIRED_ENV, service_class, unused_port
from openai_provider_stub import OpenAIProviderEnv, openai_provider_env, opencode_provider_stub


def test_public_service_import_and_init_signature_uses_env_only() -> None:
    service = service_class()
    signature = inspect.signature(service)

    assert service.__name__ == "OpenAIProviderLoginPluginService"
    assert list(signature.parameters) == []


@pytest.mark.parametrize("missing_env", REQUIRED_ENV)
def test_init_requires_opencode_and_openai_auth_ports(
    monkeypatch: pytest.MonkeyPatch,
    missing_env: str,
) -> None:
    for env_name in REQUIRED_ENV:
        monkeypatch.setenv(env_name, str(unused_port()))
    monkeypatch.delenv(missing_env)

    with pytest.raises(ConfigurationError, match=missing_env):
        service_class()()


@pytest.mark.parametrize(
    ("env_name", "invalid_value"),
    [
        ("OPENCODE_API_PORT", "not-an-int"),
        ("OPENAI_AUTH_PORT", "not-an-int"),
        ("OPENCODE_API_PORT", "0"),
        ("OPENAI_AUTH_PORT", "0"),
        ("OPENCODE_API_PORT", "-1"),
        ("OPENAI_AUTH_PORT", "-1"),
        ("OPENCODE_API_PORT", "65536"),
        ("OPENAI_AUTH_PORT", "65536"),
    ],
)
def test_init_rejects_invalid_required_ports(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    invalid_value: str,
) -> None:
    monkeypatch.setenv("OPENCODE_API_PORT", str(unused_port()))
    monkeypatch.setenv("OPENAI_AUTH_PORT", str(unused_port()))
    monkeypatch.setenv(env_name, invalid_value)

    with pytest.raises(ConfigurationError, match=env_name):
        service_class()()


def test_init_rejects_same_opencode_and_auth_port(monkeypatch: pytest.MonkeyPatch) -> None:
    port = str(unused_port())
    monkeypatch.setenv("OPENCODE_API_PORT", port)
    monkeypatch.setenv("OPENAI_AUTH_PORT", port)

    with pytest.raises(ConfigurationError, match="OPENCODE_API_PORT.*OPENAI_AUTH_PORT"):
        service_class()()


def test_prepare_specs_publishes_auth_port_and_env_without_persistence(
    openai_provider_env: OpenAIProviderEnv,
) -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[service_class()()]
    )._prepare_specs()

    assert container_spec.ports == {
        openai_provider_env.openai_auth_port: openai_provider_env.openai_auth_port
    }
    assert container_spec.env["OPENCODE_API_PORT"] == str(openai_provider_env.opencode_api_port)
    assert container_spec.env["OPENAI_AUTH_PORT"] == str(openai_provider_env.openai_auth_port)
    assert container_spec.volumes == {}
    assert container_spec.command is None
    assert container_spec.working_dir is None
    assert not {"HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"} & set(container_spec.env)


def test_configure_container_does_not_overwrite_existing_opencode_command(
    openai_provider_env: OpenAIProviderEnv,
) -> None:
    container = ContainerSpec(
        name="contract",
        image_tag="contract:latest",
        command=["opencode", "web", "--hostname", "0.0.0.0"],
    )

    service_class()().configure_container(container)

    assert container.command == ["opencode", "web", "--hostname", "0.0.0.0"]
    assert container.ports[openai_provider_env.openai_auth_port] == (
        openai_provider_env.openai_auth_port
    )


def test_openai_auth_runs_as_composable_managed_process(
    openai_provider_env: OpenAIProviderEnv,
) -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[service_class()()]
    )._prepare_specs()

    managed_processes = getattr(container_spec, "managed_processes", None)
    assert managed_processes is not None, "OpenAI auth must not overwrite OpenCode process"
    assert any("OPENAI_AUTH_PORT" in repr(process) for process in managed_processes)
