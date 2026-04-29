from __future__ import annotations

import shlex
from pathlib import PurePosixPath

from assistant_api.models import ContainerRuntimeContext, ContainerSpec, ImageSpec, VolumeMount


class OpenCodePersistencePluginService:
    name = "opencode-persistence"

    def __init__(
        self,
        config_volume: str = "notes_assistant_api_opencode_config",
        data_volume: str = "notes_assistant_api_opencode_data",
        home: PurePosixPath = PurePosixPath("/tmp/opencode-home"),
    ) -> None:
        self.config_volume = config_volume
        self.data_volume = data_volume
        self.home = home

    def configure_image(self, image: ImageSpec) -> None:
        return None

    def configure_container(self, container: ContainerSpec) -> None:
        config_home = self.home / ".config"
        data_home = self.home / ".local/share"
        container.env.update(
            {
                "HOME": str(self.home),
                "XDG_CONFIG_HOME": str(config_home),
                "XDG_DATA_HOME": str(data_home),
            }
        )
        container.volumes[self.config_volume] = VolumeMount(
            source=self.config_volume,
            target=config_home / "opencode",
            type="volume",
        )
        container.volumes[self.data_volume] = VolumeMount(
            source=self.data_volume,
            target=data_home / "opencode",
            type="volume",
        )

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        result = runtime.exec(
            [
                "/bin/sh",
                "-lc",
                _persistence_health_command(
                    str(self.home),
                    str(self.home / ".config" / "opencode"),
                    str(self.home / ".local/share" / "opencode"),
                ),
            ]
        )
        if result.exit_code != 0:
            raise RuntimeError(f"OpenCode persistence health check failed: {result.output}")


def _persistence_health_command(home: str, config_dir: str, data_dir: str) -> str:
    return "\n".join(
        [
            "set -eu",
            f"home={shlex.quote(home)}",
            f"config_dir={shlex.quote(config_dir)}",
            f"data_dir={shlex.quote(data_dir)}",
            'test -d "$home"',
            'test -d "$config_dir"',
            'test -d "$data_dir"',
            'for target in "$config_dir" "$data_dir"; do',
            '  probe="$target/.notes-assistant-persistence-health-$$"',
            '  printf "%s" ok > "$probe"',
            '  test "$(cat "$probe")" = ok',
            '  rm -f "$probe"',
            "done",
        ]
    )
