from __future__ import annotations

import shlex
from pathlib import Path, PurePosixPath

from assistant_api.container_builder.container_plugin import MOUNT_METADATA_STATE_KEY
from assistant_api.models import (
    ContainerRuntimeContext,
    ContainerSpec,
    ImageSpec,
    MountMetadata,
    VolumeMount,
)


class LocalDirMountPluginService:
    name = "local-dir-mount"

    def __init__(
        self,
        host_path: str | Path,
        container_path: PurePosixPath = PurePosixPath("/workspace/project"),
        mode: str = "rw",
    ) -> None:
        self.host_path = Path(host_path).expanduser().resolve()
        self.container_path = container_path
        self.mode = mode

    def configure_image(self, image: ImageSpec) -> None:
        return None

    def configure_container(self, container: ContainerSpec) -> None:
        source_key = str(self.host_path)
        container.volumes[source_key] = VolumeMount(
            source=source_key,
            target=self.container_path,
            mode=self.mode,
            type="bind",
        )
        container.state[MOUNT_METADATA_STATE_KEY] = MountMetadata(
            host_path=self.host_path,
            host_basename=self.host_path.name or "mounted_dir",
            source_key=source_key,
            container_path=self.container_path,
            mode=self.mode,
        )

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        result = runtime.exec(
            [
                "/bin/sh",
                "-lc",
                _mount_health_command(str(self.container_path), self.mode),
            ]
        )
        if result.exit_code != 0:
            raise RuntimeError(f"local directory mount health check failed: {result.output}")


def _mount_health_command(container_path: str, mode: str) -> str:
    quoted_path = shlex.quote(container_path)
    commands = [
        "set -eu",
        f"mount_path={quoted_path}",
        'test -d "$mount_path"',
        'test -r "$mount_path"',
    ]
    if mode != "ro":
        commands.extend(
            [
                'probe="$mount_path/.notes-assistant-local-mount-health-$$"',
                'trap \'rm -f "$probe"\' EXIT INT TERM',
                "printf '%s' ok > \"$probe\"",
                'test "$(cat "$probe")" = ok',
                'rm -f "$probe"',
                "trap - EXIT INT TERM",
            ]
        )
    return "\n".join(commands)
