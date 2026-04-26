from __future__ import annotations

from typing import Protocol

from assistant_api.models import ContainerRuntimeContext, ContainerSpec, ImageSpec


MOUNT_METADATA_STATE_KEY = "mount"


class ContainerPluginService(Protocol):
    name: str

    def configure_image(self, image: ImageSpec) -> None: ...

    def configure_container(self, container: ContainerSpec) -> None: ...

    def post_start(self, runtime: ContainerRuntimeContext) -> None: ...
