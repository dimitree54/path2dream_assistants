from __future__ import annotations

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
        return None
