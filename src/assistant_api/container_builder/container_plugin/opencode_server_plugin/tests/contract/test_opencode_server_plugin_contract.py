from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin import OPENCODE_RUNTIME_STATE_KEY
from assistant_api.container_builder.container_plugin.opencode_server_plugin import (
    OpenCodeServerPluginService,
)
from assistant_api.models import ContainerRuntimeContext, ImageSpec, OpenCodeRuntimeMetadata


def test_opencode_server_plugin_adds_command_and_port_without_persistence() -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[OpenCodeServerPluginService(host_port=4097)]
    )._prepare_specs()

    assert container_spec.command == [
        "opencode",
        "serve",
        "--hostname",
        "0.0.0.0",
        "--port",
        "4096",
    ]
    assert container_spec.ports == {4096: 4097}
    assert container_spec.env == {}
    assert container_spec.volumes == {}
    assert container_spec.working_dir == PurePosixPath("/workspace")
    runtime = container_spec.state[OPENCODE_RUNTIME_STATE_KEY]
    assert isinstance(runtime, OpenCodeRuntimeMetadata)
    assert runtime.working_dir == PurePosixPath("/workspace")
    assert runtime.api_container_port == 4096


def test_opencode_server_plugin_uses_configured_port_and_existing_working_dir() -> None:
    plugin = OpenCodeServerPluginService(host_port=5097, container_port=5096)
    _image_spec, container_spec = ContainerBuilderService(plugins=[])._prepare_specs()
    container_spec.working_dir = PurePosixPath("/workspace/project")

    plugin.configure_container(container_spec)

    assert container_spec.command == [
        "opencode",
        "serve",
        "--hostname",
        "0.0.0.0",
        "--port",
        "5096",
    ]
    assert container_spec.ports == {5096: 5097}
    runtime = container_spec.state[OPENCODE_RUNTIME_STATE_KEY]
    assert isinstance(runtime, OpenCodeRuntimeMetadata)
    assert runtime.working_dir == PurePosixPath("/workspace/project")
    assert runtime.api_container_port == 5096


def test_opencode_server_plugin_does_not_configure_image_and_self_checks_post_start() -> None:
    plugin = OpenCodeServerPluginService(host_port=4097)
    image_spec = ImageSpec()

    assert plugin.configure_image(image_spec) is None
    assert image_spec.run_commands == []
    container = _SuccessfulExecContainer()
    plugin.post_start(
        ContainerRuntimeContext(docker_client=object(), container=container, state={})
    )
    assert container.commands
    assert "/global/health" in container.commands[0][2]


def test_opencode_server_post_start_fails_when_health_probe_fails() -> None:
    plugin = OpenCodeServerPluginService(host_port=4097)

    with pytest.raises(RuntimeError, match="OpenCode server health check failed"):
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
