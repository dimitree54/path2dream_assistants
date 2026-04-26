from __future__ import annotations

from pathlib import PurePosixPath

from assistant_api.models import ContainerRuntimeContext, ContainerSpec, ImageSpec, VolumeMount


class GoogleDrivePersistencePluginService:
    name = "google-drive-persistence"

    def __init__(
        self,
        config_volume: str = "notes_assistant_api_google_drive_config",
        cache_volume: str = "notes_assistant_api_google_drive_cache",
        config_dir: PurePosixPath = PurePosixPath("/tmp/google-drive-persistence/rclone-config"),
        cache_dir: PurePosixPath = PurePosixPath("/tmp/google-drive-persistence/rclone-cache"),
    ) -> None:
        self.config_volume = config_volume
        self.cache_volume = cache_volume
        self.config_dir = config_dir
        self.cache_dir = cache_dir

    def configure_image(self, image: ImageSpec) -> None:
        return None

    def configure_container(self, container: ContainerSpec) -> None:
        container.env["RCLONE_CONFIG"] = str(self.config_dir / "rclone.conf")
        container.env["RCLONE_CACHE_DIR"] = str(self.cache_dir)
        container.volumes[self.config_volume] = VolumeMount(
            source=self.config_volume,
            target=self.config_dir,
            type="volume",
        )
        container.volumes[self.cache_volume] = VolumeMount(
            source=self.cache_volume,
            target=self.cache_dir,
            type="volume",
        )

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        return None
