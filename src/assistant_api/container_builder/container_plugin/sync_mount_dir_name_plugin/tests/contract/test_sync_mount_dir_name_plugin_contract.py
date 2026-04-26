from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.container_builder.container_plugin.local_dir_mount_plugin import LocalDirMountPluginService
from assistant_api.container_builder.container_plugin.sync_mount_dir_name_plugin import (
    SyncMountDirNamePluginService,
)


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
