from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.container_builder.container_plugin.local_dir_mount_plugin import LocalDirMountPluginService
from assistant_api.models import ContainerExecutionIdentity, ContainerRuntimeContext, VolumeMount


def test_local_dir_mount_plugin_adds_bind_mount_and_metadata(tmp_path: Path) -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[LocalDirMountPluginService(tmp_path)]
    )._prepare_specs()

    mount = container_spec.volumes[str(tmp_path)]
    assert mount == VolumeMount(
        source=str(tmp_path),
        target=PurePosixPath("/workspace"),
        mode="rw",
        type="bind",
    )
    assert container_spec.state["mount"].host_basename == tmp_path.name
    assert container_spec.working_dir is None


def test_local_dir_mount_plugin_supports_workspace_subdir_name(tmp_path: Path) -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[LocalDirMountPluginService(tmp_path, workspace_subdir_name="notes")]
    )._prepare_specs()

    assert container_spec.volumes[str(tmp_path)].target == PurePosixPath("/workspace/notes")
    assert container_spec.state["mount"].container_path == PurePosixPath("/workspace/notes")


def test_local_dir_mount_plugin_supports_explicit_container_path(tmp_path: Path) -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[
            LocalDirMountPluginService(
                tmp_path,
                container_path=PurePosixPath("/mnt/project"),
            )
        ]
    )._prepare_specs()

    assert container_spec.volumes[str(tmp_path)].target == PurePosixPath("/mnt/project")
    assert container_spec.state["mount"].container_path == PurePosixPath("/mnt/project")


@pytest.mark.parametrize("workspace_subdir_name", ["", " ", ".", "..", "/notes", "nested/notes", "nested\\notes"])
def test_local_dir_mount_plugin_rejects_invalid_workspace_subdir_name(
    tmp_path: Path,
    workspace_subdir_name: str,
) -> None:
    with pytest.raises(ConfigurationError, match="workspace_subdir_name"):
        LocalDirMountPluginService(tmp_path, workspace_subdir_name=workspace_subdir_name)


def test_local_dir_mount_plugin_rejects_workspace_subdir_name_with_container_path(
    tmp_path: Path,
) -> None:
    with pytest.raises(ConfigurationError, match="mutually exclusive"):
        LocalDirMountPluginService(
            tmp_path,
            workspace_subdir_name="notes",
            container_path=PurePosixPath("/mnt/project"),
        )


def test_writable_mount_rejects_incompatible_execution_identity(tmp_path: Path) -> None:
    incompatible_uid = tmp_path.stat().st_uid + 1
    identity = ContainerExecutionIdentity(
        uid=incompatible_uid,
        gid=tmp_path.stat().st_gid,
        umask=0o022,
    )

    with pytest.raises(ConfigurationError, match="ownership"):
        ContainerBuilderService(
            plugins=[LocalDirMountPluginService(tmp_path)],
            execution_identity=identity,
        )._prepare_specs()


def test_local_dir_mount_post_start_checks_mount_health(tmp_path: Path) -> None:
    plugin = LocalDirMountPluginService(tmp_path)
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
    assert str(plugin.container_path) in container.commands[0][2]


def test_local_dir_mount_post_start_fails_when_mount_probe_fails(tmp_path: Path) -> None:
    plugin = LocalDirMountPluginService(tmp_path)
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()

    with pytest.raises(RuntimeError, match="local directory mount health check failed"):
        plugin.post_start(
            ContainerRuntimeContext(
                docker_client=object(),
                container=_RecordingContainer(exit_code=1, output="not writable"),
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
