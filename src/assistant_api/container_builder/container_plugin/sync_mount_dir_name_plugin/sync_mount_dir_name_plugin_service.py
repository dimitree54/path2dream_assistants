from __future__ import annotations

import shlex
from pathlib import PurePosixPath

from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.container_builder.container_plugin import MOUNT_METADATA_STATE_KEY
from assistant_api.models import (
    ContainerRuntimeContext,
    ContainerSpec,
    ImageSpec,
    MountMetadata,
    VolumeMount,
)


class SyncMountDirNamePluginService:
    name = "sync-mount-dir-name"

    def __init__(
        self,
        working_dir: PurePosixPath = PurePosixPath("/workspace/workdir"),
        mounted_source: PurePosixPath = PurePosixPath("/workspace/mounted-source"),
    ) -> None:
        self.working_dir = working_dir
        self.mounted_source = mounted_source

    def configure_image(self, image: ImageSpec) -> None:
        return None

    def configure_container(self, container: ContainerSpec) -> None:
        mount = self._mount_metadata(container.state)
        container.volumes[mount.source_key] = VolumeMount(
            source=mount.source_key,
            target=self.mounted_source,
            mode=mount.mode,
            type="bind",
        )
        container.working_dir = self.working_dir
        container.state[MOUNT_METADATA_STATE_KEY] = MountMetadata(
            host_path=mount.host_path,
            host_basename=mount.host_basename,
            source_key=mount.source_key,
            container_path=self.mounted_source,
            mode=mount.mode,
        )

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        mount = self._mount_metadata(runtime.state)
        link_path = self.working_dir / mount.host_basename
        command = (
            f"mkdir -p {shlex.quote(str(self.working_dir))} "
            f"&& rm -f {shlex.quote(str(link_path))} "
            f"&& ln -sfn {shlex.quote(str(self.mounted_source))} {shlex.quote(str(link_path))} "
            f"&& test -d {shlex.quote(str(self.mounted_source))} "
            f"&& test -L {shlex.quote(str(link_path))} "
            f"&& test \"$(readlink {shlex.quote(str(link_path))})\" = {shlex.quote(str(self.mounted_source))}"
        )
        result = runtime.exec(["/bin/sh", "-lc", command])
        if result.exit_code != 0:
            raise RuntimeError(f"failed to sync mount dir name: {result.output}")

    @staticmethod
    def _mount_metadata(state: dict[str, object]) -> MountMetadata:
        mount = state.get(MOUNT_METADATA_STATE_KEY)
        if not isinstance(mount, MountMetadata):
            raise ConfigurationError("SyncMountDirNamePluginService requires mount metadata")
        return mount
