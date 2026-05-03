from __future__ import annotations

import inspect
from pathlib import PurePosixPath

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin import (
    OPENCODE_RUNTIME_STATE_KEY,
)
from assistant_api.container_builder.container_plugin.opencode_web_server_plugin import (
    OpenCodeWebServerPluginService,
)
from assistant_api.models import (
    ContainerRuntimeContext,
    OpenCodeRuntimeMetadata,
    PublishedPort,
)


def test_opencode_web_server_init_signature_defaults() -> None:
    signature = inspect.signature(OpenCodeWebServerPluginService)

    assert list(signature.parameters) == [
        "host_port",
        "container_port",
        "wait_for_mount",
        "host",
    ]
    assert signature.parameters["host_port"].default == 4096
    assert signature.parameters["container_port"].default == 4096
    assert signature.parameters["wait_for_mount"].default is False
    assert signature.parameters["host"].default is None


def test_opencode_web_server_plugin_adds_mount_gated_command_and_port_without_persistence() -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[OpenCodeWebServerPluginService(host_port=4097)]
    )._prepare_specs()

    assert container_spec.command is not None
    assert container_spec.command[:2] == ["/bin/sh", "-lc"]
    command_text = container_spec.command[2]
    assert "mountpoint -q" in command_text
    assert "Required mount is not ready" in command_text
    assert "opencode web --hostname 0.0.0.0 --port 4096" in command_text
    assert container_spec.ports == {4096: 4097}
    assert container_spec.env == {}
    assert container_spec.volumes == {}
    assert container_spec.working_dir == PurePosixPath("/workspace")
    runtime = container_spec.state[OPENCODE_RUNTIME_STATE_KEY]
    assert isinstance(runtime, OpenCodeRuntimeMetadata)
    assert runtime.working_dir == PurePosixPath("/workspace")
    assert runtime.api_container_port == 4096


def test_opencode_web_server_plugin_supports_host_bind_address() -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[OpenCodeWebServerPluginService(host_port=4097, host="127.0.0.1")]
    )._prepare_specs()

    assert container_spec.ports == {
        4096: PublishedPort(host_port=4097, host="127.0.0.1")
    }


def test_opencode_web_server_plugin_rejects_invalid_host_bind_address() -> None:
    with pytest.raises(Exception, match="host"):
        OpenCodeWebServerPluginService(host_port=4097, host="localhost")


def test_opencode_web_server_wait_for_mount_uses_infinite_wait_loop() -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[OpenCodeWebServerPluginService(host_port=4097, wait_for_mount=True)]
    )._prepare_specs()

    assert container_spec.command is not None
    command_text = container_spec.command[2]
    assert "while ! mountpoint -q" in command_text
    assert "Waiting for mounted path" in command_text
    assert "Required mount is not ready" not in command_text


def test_opencode_web_server_post_start_checks_container_health() -> None:
    plugin = OpenCodeWebServerPluginService(host_port=4097)
    container = _SuccessfulExecContainer()

    plugin.post_start(
        ContainerRuntimeContext(docker_client=object(), container=container, state={})
    )

    assert container.commands
    assert "mountpoint -q" in container.commands[0][2]
    assert "Required mount is not ready" in container.commands[0][2]
    assert "/global/health" in container.commands[0][2]


def test_opencode_web_server_wait_for_mount_post_start_uses_infinite_wait_loop() -> None:
    plugin = OpenCodeWebServerPluginService(host_port=4097, wait_for_mount=True)
    container = _SuccessfulExecContainer()

    plugin.post_start(
        ContainerRuntimeContext(docker_client=object(), container=container, state={})
    )

    assert "while ! mountpoint -q" in container.commands[0][2]
    assert "Waiting for mounted path" in container.commands[0][2]


def test_opencode_web_server_post_start_fails_when_health_probe_fails() -> None:
    plugin = OpenCodeWebServerPluginService(host_port=4097)

    with pytest.raises(RuntimeError, match="OpenCode web health check failed"):
        plugin.post_start(
            ContainerRuntimeContext(
                docker_client=object(),
                container=_SuccessfulExecContainer(exit_code=1, output="not ready"),
                state={},
            )
        )


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
