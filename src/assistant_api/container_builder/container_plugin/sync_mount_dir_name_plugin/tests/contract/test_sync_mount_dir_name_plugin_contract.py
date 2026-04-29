from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.container_builder.container_plugin.local_dir_mount_plugin import LocalDirMountPluginService
from assistant_api.container_builder.container_plugin.sync_mount_dir_name_plugin import (
    SyncMountDirNamePluginService,
)
from assistant_api.models import ContainerRuntimeContext


def test_sync_mount_dir_name_plugin_requires_mount_plugin() -> None:
    with pytest.raises(ConfigurationError, match="requires mount metadata"):
        ContainerBuilderService(plugins=[SyncMountDirNamePluginService()])._prepare_specs()


def test_sync_mount_dir_name_plugin_rewrites_mount_target_and_workdir(tmp_path: Path) -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[LocalDirMountPluginService(tmp_path), SyncMountDirNamePluginService()]
    )._prepare_specs()

    assert container_spec.volumes[str(tmp_path)].target == PurePosixPath(
        "/workspace/mounted-source"
    )
    assert container_spec.working_dir == PurePosixPath("/workspace/workdir")
    assert container_spec.state["mount"].container_path == PurePosixPath("/workspace/mounted-source")


def test_sync_mount_dir_name_post_start_creates_and_verifies_symlink(tmp_path: Path) -> None:
    plugin = SyncMountDirNamePluginService()
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[LocalDirMountPluginService(tmp_path), plugin]
    )._prepare_specs()
    container = _RecordingContainer(exit_code=0)

    plugin.post_start(
        ContainerRuntimeContext(
            docker_client=object(),
            container=container,
            state=container_spec.state,
        )
    )

    assert container.commands
    assert "readlink" in container.commands[0][2]


def test_sync_mount_dir_name_post_start_fails_when_symlink_check_fails(
    tmp_path: Path,
) -> None:
    plugin = SyncMountDirNamePluginService()
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[LocalDirMountPluginService(tmp_path), plugin]
    )._prepare_specs()

    with pytest.raises(RuntimeError, match="failed to sync mount dir name"):
        plugin.post_start(
            ContainerRuntimeContext(
                docker_client=object(),
                container=_RecordingContainer(exit_code=1, output="bad link"),
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
