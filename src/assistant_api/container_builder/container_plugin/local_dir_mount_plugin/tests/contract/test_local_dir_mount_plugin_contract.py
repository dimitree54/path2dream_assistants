from __future__ import annotations

from pathlib import Path, PurePosixPath

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin.local_dir_mount_plugin import LocalDirMountPluginService
from assistant_api.models import VolumeMount


def test_local_dir_mount_plugin_adds_bind_mount_and_metadata(tmp_path: Path) -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[LocalDirMountPluginService(tmp_path)]
    )._prepare_specs()

    mount = container_spec.volumes[str(tmp_path)]
    assert mount == VolumeMount(
        source=str(tmp_path),
        target=PurePosixPath("/workspace/project"),
        mode="rw",
        type="bind",
    )
    assert container_spec.state["mount"].host_basename == tmp_path.name
    assert container_spec.working_dir is None
