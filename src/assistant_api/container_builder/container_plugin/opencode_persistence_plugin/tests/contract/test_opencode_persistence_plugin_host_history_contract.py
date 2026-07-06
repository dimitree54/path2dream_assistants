from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.container_builder.container_plugin.opencode_persistence_plugin import (
    OpenCodePersistencePluginService,
)
from assistant_api.models import ContainerRuntimeContext, VolumeMount


HISTORY_TARGET = PurePosixPath("/tmp/notes-assistant/opencode-persistence/history")
HISTORY_DB = "/tmp/notes-assistant/opencode-persistence/history/opencode.db"


def test_host_history_backend_adds_bind_mount_and_opencode_db(
    tmp_path: Path,
) -> None:
    history_dir = tmp_path / "history"
    history_dir.mkdir()

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[
            OpenCodePersistencePluginService(
                config_volume="test_oc_config",
                data_volume="test_oc_data",
                persist_auth=False,
                persist_chat_history=True,
                persist_opencode_artifacts=False,
                persist_skills=False,
                persist_agents=False,
                chat_history_host_dir=history_dir,
            )
        ]
    )._prepare_specs()

    source = str(history_dir.resolve())
    assert container_spec.env["OPENCODE_DB"] == HISTORY_DB
    assert container_spec.volumes == {
        source: VolumeMount(
            source=source,
            target=HISTORY_TARGET,
            mode="rw",
            type="bind",
        )
    }
    assert "test_oc_data_history" not in container_spec.volumes
    assert len(container_spec.startup_tasks) == 1
    setup_command = container_spec.startup_tasks[0].command[2]
    assert "storage" in setup_command
    assert "auth.json" not in setup_command


def test_host_history_backend_avoids_full_directory_shortcut_with_default_flags(
    tmp_path: Path,
) -> None:
    history_dir = tmp_path / "history"
    history_dir.mkdir()

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[
            OpenCodePersistencePluginService(
                config_volume="test_oc_config",
                data_volume="test_oc_data",
                chat_history_host_dir=history_dir,
            )
        ]
    )._prepare_specs()

    assert container_spec.env["OPENCODE_DB"] == HISTORY_DB
    assert "test_oc_config" not in container_spec.volumes
    assert "test_oc_data" not in container_spec.volumes
    assert "test_oc_data_history" not in container_spec.volumes
    assert str(history_dir.resolve()) in container_spec.volumes
    assert {
        "test_oc_data_auth",
        "test_oc_config_artifacts",
        "test_oc_config_skills",
        "test_oc_config_agents",
    }.issubset(container_spec.volumes)
    assert container_spec.volumes[str(history_dir.resolve())].target == HISTORY_TARGET


def test_host_history_backend_keeps_auth_and_history_isolated(
    tmp_path: Path,
) -> None:
    history_dir = tmp_path / "history"
    history_dir.mkdir()

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
                chat_history_host_dir=history_dir,
            )
        ]
    )._prepare_specs()

    assert set(container_spec.volumes) == {
        str(history_dir.resolve()),
        "test_oc_data_auth",
    }
    assert container_spec.volumes["test_oc_data_auth"].target == PurePosixPath(
        "/tmp/notes-assistant/opencode-persistence/auth"
    )
    assert container_spec.volumes[str(history_dir.resolve())].target == HISTORY_TARGET
    assert "auth_dir=/tmp/notes-assistant/opencode-persistence/auth" in (
        container_spec.startup_tasks[0].command[2]
    )
    assert "auth_dir=/tmp/notes-assistant/opencode-persistence/history" not in (
        container_spec.startup_tasks[0].command[2]
    )
    assert "ln -s \"$auth_dir/auth.json\" \"$data_dir/auth.json\"" in (
        container_spec.startup_tasks[0].command[2]
    )


def test_host_history_backend_post_start_checks_mounted_history_target(
    tmp_path: Path,
) -> None:
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    plugin = OpenCodePersistencePluginService(
        config_volume="test_oc_config",
        data_volume="test_oc_data",
        persist_auth=False,
        persist_chat_history=True,
        persist_opencode_artifacts=False,
        persist_skills=False,
        persist_agents=False,
        chat_history_host_dir=history_dir,
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
    assert str(HISTORY_TARGET) in container.commands[0][2]


def test_host_history_backend_requires_chat_history_enabled(tmp_path: Path) -> None:
    history_dir = tmp_path / "history"
    history_dir.mkdir()

    with pytest.raises(ConfigurationError, match="chat_history_host_dir"):
        OpenCodePersistencePluginService(
            config_volume="test_oc_config",
            data_volume="test_oc_data",
            persist_chat_history=False,
            chat_history_host_dir=history_dir,
        )


def test_host_history_backend_rejects_missing_host_directory(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="chat_history_host_dir"):
        OpenCodePersistencePluginService(
            config_volume="test_oc_config",
            data_volume="test_oc_data",
            chat_history_host_dir=tmp_path / "missing",
        )


def test_host_history_backend_rejects_file_path(tmp_path: Path) -> None:
    file_path = tmp_path / "history-file"
    file_path.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="chat_history_host_dir"):
        OpenCodePersistencePluginService(
            config_volume="test_oc_config",
            data_volume="test_oc_data",
            chat_history_host_dir=file_path,
        )


def test_host_history_backend_rejects_invalid_type() -> None:
    with pytest.raises(ConfigurationError, match="chat_history_host_dir"):
        OpenCodePersistencePluginService(
            config_volume="test_oc_config",
            data_volume="test_oc_data",
            chat_history_host_dir=object(),
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
