from __future__ import annotations

import inspect
from pathlib import PurePosixPath

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.container_builder.container_plugin import OPENCODE_RUNTIME_STATE_KEY
from assistant_api.container_builder.container_plugin.opencode_persistence_plugin import (
    OpenCodePersistencePluginService,
)
from assistant_api.container_builder.container_plugin.opencode_server_plugin import (
    OpenCodeServerPluginService,
)
from assistant_api.models import (
    ContainerRuntimeContext,
    ContainerSpec,
    OpenCodeRuntimeMetadata,
    PublishedPort,
)
from openai_provider_login_contract_helpers import (
    OpenCodeRuntimeStatePlugin,
    service_class,
    unused_port,
)
from openai_provider_stub import OpenAIProviderEnv, openai_provider_env, opencode_provider_stub


def test_public_service_import_and_init_signature_uses_init_ports() -> None:
    service = service_class()
    signature = inspect.signature(service)

    assert service.__name__ == "OpenAIProviderLoginPluginService"
    assert list(signature.parameters) == [
        "host_port",
        "auth_container_port",
        "opencode_model",
        "host",
    ]
    assert signature.parameters["host_port"].default is inspect.Parameter.empty
    assert signature.parameters["auth_container_port"].default is None
    assert signature.parameters["opencode_model"].default == "openai/gpt-5.5"
    assert signature.parameters["host"].default is None


def test_init_does_not_require_opencode_or_openai_auth_port_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENCODE_API_PORT", raising=False)
    monkeypatch.delenv("OPENAI_AUTH_PORT", raising=False)

    plugin = service_class()(host_port=unused_port())

    assert plugin.host_port > 0
    assert plugin.opencode_model == "openai/gpt-5.5"


@pytest.mark.parametrize(
    ("kwargs", "expected_message"),
    [
        ({"host_port": 0}, "host_port"),
        ({"host_port": -1}, "host_port"),
        ({"host_port": 65536}, "host_port"),
        ({"host_port": "not-an-int"}, "host_port"),
        ({"auth_container_port": 0}, "auth_container_port"),
        ({"auth_container_port": -1}, "auth_container_port"),
        ({"auth_container_port": 65536}, "auth_container_port"),
        ({"auth_container_port": "not-an-int"}, "auth_container_port"),
        ({"host": "localhost"}, "host"),
    ],
)
def test_init_rejects_invalid_ports(
    kwargs: dict[str, object],
    expected_message: str,
) -> None:
    init_kwargs = {"host_port": unused_port(), **kwargs}

    with pytest.raises(ConfigurationError, match=expected_message):
        service_class()(**init_kwargs)


@pytest.mark.parametrize(
    "opencode_model",
    [
        "",
        "   ",
        "gpt-5.5",
        "anthropic/claude-sonnet-4-5",
        123,
    ],
)
def test_init_rejects_invalid_opencode_model(opencode_model: object) -> None:
    with pytest.raises(ConfigurationError, match="opencode_model"):
        service_class()(host_port=unused_port(), opencode_model=opencode_model)


def test_configure_container_requires_opencode_runtime_state(
    openai_provider_env: OpenAIProviderEnv,
) -> None:
    plugin = service_class()(host_port=openai_provider_env.openai_auth_port)

    with pytest.raises(ConfigurationError, match="OpenCode runtime metadata"):
        ContainerBuilderService(plugins=[plugin])._prepare_specs()


def test_configure_container_rejects_same_opencode_and_auth_port(
    openai_provider_env: OpenAIProviderEnv,
) -> None:
    plugin = service_class()(
        host_port=unused_port(),
        auth_container_port=openai_provider_env.opencode_api_port,
    )

    with pytest.raises(ConfigurationError, match="OpenCode API port.*OpenAI auth port"):
        ContainerBuilderService(
            plugins=[
                OpenCodeRuntimeStatePlugin(openai_provider_env.opencode_api_port),
                plugin,
            ]
        )._prepare_specs()


def test_prepare_specs_publishes_auth_port_and_env_without_persistence(
    openai_provider_env: OpenAIProviderEnv,
) -> None:
    plugin = service_class()(host_port=openai_provider_env.openai_auth_port)

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[
            OpenCodeRuntimeStatePlugin(openai_provider_env.opencode_api_port),
            plugin,
        ]
    )._prepare_specs()

    assert container_spec.ports == {
        openai_provider_env.openai_auth_port: openai_provider_env.openai_auth_port
    }
    assert container_spec.env["OPENCODE_API_PORT"] == str(openai_provider_env.opencode_api_port)
    assert container_spec.env["OPENAI_AUTH_PORT"] == str(openai_provider_env.openai_auth_port)
    assert container_spec.env["OPENCODE_MODEL"] == "openai/gpt-5.5"
    assert container_spec.volumes == {}
    assert container_spec.command is None
    assert container_spec.working_dir is None
    assert not {"HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"} & set(container_spec.env)
    assert len(container_spec.startup_tasks) == 1
    assert container_spec.startup_tasks[0].name == "openai-opencode-default-model"
    assert container_spec.startup_tasks[0].command[-1] == "openai/gpt-5.5"
    assert "_opencode_config.py" in container_spec.startup_tasks[0].command[-2]


def test_prepare_specs_supports_host_bind_address(
    openai_provider_env: OpenAIProviderEnv,
) -> None:
    plugin = service_class()(
        host_port=openai_provider_env.openai_auth_port,
        host="127.0.0.1",
    )

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[
            OpenCodeRuntimeStatePlugin(openai_provider_env.opencode_api_port),
            plugin,
        ]
    )._prepare_specs()

    assert container_spec.ports == {
        openai_provider_env.openai_auth_port: PublishedPort(
            host_port=openai_provider_env.openai_auth_port,
            host="127.0.0.1",
        )
    }


def test_prepare_specs_uses_custom_opencode_model(
    openai_provider_env: OpenAIProviderEnv,
) -> None:
    plugin = service_class()(
        host_port=openai_provider_env.openai_auth_port,
        opencode_model="openai/gpt-5.5-fast",
    )

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[
            OpenCodeRuntimeStatePlugin(openai_provider_env.opencode_api_port),
            plugin,
        ]
    )._prepare_specs()

    assert plugin.opencode_model == "openai/gpt-5.5-fast"
    assert container_spec.env["OPENCODE_MODEL"] == "openai/gpt-5.5-fast"
    assert container_spec.startup_tasks[0].command[-1] == "openai/gpt-5.5-fast"


def test_configure_container_does_not_overwrite_existing_opencode_command(
    openai_provider_env: OpenAIProviderEnv,
) -> None:
    container = ContainerSpec(
        name="contract",
        image_tag="contract:latest",
        command=["opencode", "web", "--hostname", "0.0.0.0"],
    )
    container.state[OPENCODE_RUNTIME_STATE_KEY] = OpenCodeRuntimeMetadata(
        working_dir=PurePosixPath("/workspace"),
        api_container_port=openai_provider_env.opencode_api_port,
    )

    service_class()(host_port=openai_provider_env.openai_auth_port).configure_container(container)

    assert container.command == ["opencode", "web", "--hostname", "0.0.0.0"]
    assert container.ports[openai_provider_env.openai_auth_port] == (
        openai_provider_env.openai_auth_port
    )


def test_openai_auth_runs_as_composable_managed_process(
    openai_provider_env: OpenAIProviderEnv,
) -> None:
    plugin = service_class()(host_port=openai_provider_env.openai_auth_port)

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[
            OpenCodeRuntimeStatePlugin(openai_provider_env.opencode_api_port),
            plugin,
        ]
    )._prepare_specs()

    managed_processes = getattr(container_spec, "managed_processes", None)
    assert managed_processes is not None, "OpenAI auth must not overwrite OpenCode process"
    assert any("OPENAI_AUTH_PORT" in repr(process) for process in managed_processes)


def test_openai_auth_waits_for_opencode_with_bounded_health_probe(
    openai_provider_env: OpenAIProviderEnv,
) -> None:
    plugin = service_class()(host_port=openai_provider_env.openai_auth_port)

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[
            OpenCodeRuntimeStatePlugin(openai_provider_env.opencode_api_port),
            plugin,
        ]
    )._prepare_specs()

    process = next(
        process
        for process in container_spec.managed_processes
        if process.name == "openai-provider-login"
    )
    command_text = process.command[2]

    assert (
        "while ! wget -q -T 5 -O - "
        "http://127.0.0.1:$OPENCODE_API_PORT/global/health"
    ) in command_text
    assert "exec python3 /opt/notes-assistant-api/openai_provider_auth_server.py" in command_text


def test_post_start_does_not_start_host_side_auth_server(
    openai_provider_env: OpenAIProviderEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from assistant_api.container_builder.container_plugin.openai_provider_login_plugin._auth_server import (
        OpenAIProviderAuthServer,
    )

    plugin = service_class()(host_port=openai_provider_env.openai_auth_port)
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[
            OpenCodeRuntimeStatePlugin(openai_provider_env.opencode_api_port),
            plugin,
        ]
    )._prepare_specs()

    def fail_host_start(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("OpenAI auth must not start host-side server")

    monkeypatch.setattr(OpenAIProviderAuthServer, "start_in_thread", fail_host_start)

    container = _SuccessfulExecContainer()
    plugin.post_start(
        ContainerRuntimeContext(
            docker_client=object(),
            container=container,
            state=container_spec.state,
        )
    )

    assert container.commands
    assert "/status" in container.commands[0][2]


def test_post_start_fails_when_openai_auth_status_is_unhealthy(
    openai_provider_env: OpenAIProviderEnv,
) -> None:
    plugin = service_class()(host_port=openai_provider_env.openai_auth_port)
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[
            OpenCodeRuntimeStatePlugin(openai_provider_env.opencode_api_port),
            plugin,
        ]
    )._prepare_specs()

    with pytest.raises(RuntimeError, match="OpenAI provider login health check failed"):
        plugin.post_start(
            ContainerRuntimeContext(
                docker_client=object(),
                container=_SuccessfulExecContainer(exit_code=1, output="status error"),
                state=container_spec.state,
            )
        )


def test_configure_image_installs_login_page_support_files(
    openai_provider_env: OpenAIProviderEnv,
) -> None:
    plugin = service_class()(host_port=openai_provider_env.openai_auth_port)

    image_spec, _container_spec = ContainerBuilderService(
        plugins=[
            OpenCodeRuntimeStatePlugin(openai_provider_env.opencode_api_port),
            plugin,
        ]
    )._prepare_specs()
    install_commands = "\n".join(image_spec.run_commands)

    assert image_spec.apk_packages == ["python3"]
    assert image_spec.python_packages == []
    assert "openai_provider_auth_server.py" in install_commands
    assert "_login_page.py" in install_commands
    assert "assets/petprojectcofounder_logo_small.PNG" in install_commands
    assert "assets/petprojectcofounder_login_page.css" in install_commands


def test_configure_image_keeps_dockerfile_run_commands_below_line_limit(
    openai_provider_env: OpenAIProviderEnv,
) -> None:
    plugin = service_class()(host_port=openai_provider_env.openai_auth_port)

    image_spec, _container_spec = ContainerBuilderService(
        plugins=[
            OpenCodeRuntimeStatePlugin(openai_provider_env.opencode_api_port),
            plugin,
        ]
    )._prepare_specs()

    assert max(len(command) for command in image_spec.run_commands) < 65_535


def test_prepare_specs_compose_opencode_server_persistence_and_openai_login(
    openai_provider_env: OpenAIProviderEnv,
) -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[
            OpenCodePersistencePluginService(config_volume="test_oc_config", data_volume="test_oc_data"),
            OpenCodeServerPluginService(
                host_port=openai_provider_env.opencode_api_port,
                container_port=openai_provider_env.opencode_api_port,
            ),
            service_class()(host_port=openai_provider_env.openai_auth_port),
        ]
    )._prepare_specs()

    assert container_spec.command is not None
    assert container_spec.command[:2] == ["/bin/sh", "-lc"]
    assert (
        "opencode serve --hostname 0.0.0.0 "
        f"--port {openai_provider_env.opencode_api_port}"
    ) in container_spec.command[2]
    assert container_spec.env["HOME"] == "/root"
    assert container_spec.env["XDG_CONFIG_HOME"] == "/root/.config"
    assert container_spec.env["XDG_DATA_HOME"] == "/root/.local/share"
    assert container_spec.env["OPENCODE_API_PORT"] == str(openai_provider_env.opencode_api_port)
    assert container_spec.env["OPENAI_AUTH_PORT"] == str(openai_provider_env.openai_auth_port)
    assert container_spec.ports[openai_provider_env.opencode_api_port] == (
        openai_provider_env.opencode_api_port
    )
    assert container_spec.ports[openai_provider_env.openai_auth_port] == (
        openai_provider_env.openai_auth_port
    )
    runtime = container_spec.state[OPENCODE_RUNTIME_STATE_KEY]
    assert isinstance(runtime, OpenCodeRuntimeMetadata)
    assert runtime.api_container_port == openai_provider_env.opencode_api_port
    assert len(container_spec.startup_tasks) == 1
    assert container_spec.startup_tasks[0].name == "openai-opencode-default-model"
    assert any(process.name == "openai-provider-login" for process in container_spec.managed_processes)


class _SuccessfulExecContainer:
    def __init__(self, exit_code: int = 0, output: str = "") -> None:
        self.exit_code = exit_code
        self.output = output
        self.commands: list[list[str]] = []

    def exec_run(self, command: list[str]) -> object:
        self.commands.append(command)
        exit_code = self.exit_code
        output = self.output.encode("utf-8")

        class Result:
            pass

        result = Result()
        result.exit_code = exit_code
        result.output = output
        return result
