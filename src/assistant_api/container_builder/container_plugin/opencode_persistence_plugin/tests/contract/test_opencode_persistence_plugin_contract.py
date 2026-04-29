from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin.opencode_persistence_plugin import (
    OpenCodePersistencePluginService,
)
from assistant_api.models import ContainerRuntimeContext


def test_opencode_persistence_plugin_adds_only_env_and_named_volumes() -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[OpenCodePersistencePluginService()]
    )._prepare_specs()

    assert container_spec.env == {
        "HOME": "/tmp/opencode-home",
        "XDG_CONFIG_HOME": "/tmp/opencode-home/.config",
        "XDG_DATA_HOME": "/tmp/opencode-home/.local/share",
    }
    assert container_spec.volumes["notes_assistant_api_opencode_config"].target == PurePosixPath(
        "/tmp/opencode-home/.config/opencode"
    )
    assert container_spec.volumes["notes_assistant_api_opencode_data"].target == PurePosixPath(
        "/tmp/opencode-home/.local/share/opencode"
    )
    assert container_spec.command is None
    assert container_spec.ports == {}


def test_opencode_persistence_post_start_checks_writable_state_dirs() -> None:
    plugin = OpenCodePersistencePluginService()
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()
    container = _RecordingContainer(exit_code=0)

    plugin.post_start(
        ContainerRuntimeContext(
            docker_client=object(),
            container=container,
            state=container_spec.state,
        )
    )

    assert container.commands
    assert "/tmp/opencode-home/.config/opencode" in container.commands[0][2]
    assert "/tmp/opencode-home/.local/share/opencode" in container.commands[0][2]


def test_opencode_persistence_post_start_fails_when_state_dirs_are_unhealthy() -> None:
    plugin = OpenCodePersistencePluginService()
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()

    with pytest.raises(RuntimeError, match="OpenCode persistence health check failed"):
        plugin.post_start(
            ContainerRuntimeContext(
                docker_client=object(),
                container=_RecordingContainer(exit_code=1, output="read only"),
                state=container_spec.state,
            )
        )


class _RecordingContainer:
    def __init__(self, exit_code: int, output: str = "") -> None:
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
