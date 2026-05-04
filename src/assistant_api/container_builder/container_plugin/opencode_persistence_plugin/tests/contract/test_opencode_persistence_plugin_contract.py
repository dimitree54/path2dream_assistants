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
        plugins=[OpenCodePersistencePluginService(config_volume="test_oc_config", data_volume="test_oc_data")]
    )._prepare_specs()

    assert container_spec.env == {
        "HOME": "/root",
        "XDG_CONFIG_HOME": "/root/.config",
        "XDG_DATA_HOME": "/root/.local/share",
    }
    assert container_spec.volumes["test_oc_config"].target == PurePosixPath(
        "/root/.config/opencode"
    )
    assert container_spec.volumes["test_oc_data"].target == PurePosixPath(
        "/root/.local/share/opencode"
    )
    assert container_spec.command is None
    assert container_spec.ports == {}


def test_opencode_persistence_can_persist_only_auth_and_history() -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[
            OpenCodePersistencePluginService(
                config_volume="test_oc_config",
                data_volume="test_oc_data",
                persist_auth=True,
                persist_chat_history=True,
                persist_opencode_artifacts=False,
                persist_skills=False,
                persist_agents=False,
            )
        ]
    )._prepare_specs()

    assert container_spec.env == {
        "HOME": "/root",
        "XDG_CONFIG_HOME": "/root/.config",
        "XDG_DATA_HOME": "/root/.local/share",
        "OPENCODE_DB": "/tmp/notes-assistant/opencode-persistence/history/opencode.db",
    }
    assert set(container_spec.volumes) == {
        "test_oc_data_auth",
        "test_oc_data_history",
    }
    assert container_spec.volumes[
        "test_oc_data_auth"
    ].target == PurePosixPath("/tmp/notes-assistant/opencode-persistence/auth")
    assert container_spec.volumes[
        "test_oc_data_history"
    ].target == PurePosixPath("/tmp/notes-assistant/opencode-persistence/history")
    assert len(container_spec.startup_tasks) == 1
    setup_command = container_spec.startup_tasks[0].command[2]
    assert "auth.json" in setup_command
    assert "storage" in setup_command
    assert "AGENTS.md" not in setup_command
    assert "/skills" not in setup_command
    assert "/agents" not in setup_command


def test_opencode_persistence_post_start_checks_writable_state_dirs() -> None:
    plugin = OpenCodePersistencePluginService(config_volume="test_oc_config", data_volume="test_oc_data")
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
    assert "/root/.config/opencode" in container.commands[0][2]
    assert "/root/.local/share/opencode" in container.commands[0][2]


def test_opencode_persistence_post_start_checks_enabled_granular_dirs() -> None:
    plugin = OpenCodePersistencePluginService(
        config_volume="test_oc_config",
        data_volume="test_oc_data",
        persist_opencode_artifacts=False,
        persist_skills=False,
        persist_agents=False,
    )
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
    assert "/tmp/notes-assistant/opencode-persistence/auth" in container.commands[0][2]
    assert "/tmp/notes-assistant/opencode-persistence/history" in container.commands[0][2]
    assert "/tmp/notes-assistant/opencode-persistence/skills" not in container.commands[0][2]
    assert "/tmp/notes-assistant/opencode-persistence/agents" not in container.commands[0][2]


def test_opencode_persistence_post_start_fails_when_state_dirs_are_unhealthy() -> None:
    plugin = OpenCodePersistencePluginService(config_volume="test_oc_config", data_volume="test_oc_data")
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
