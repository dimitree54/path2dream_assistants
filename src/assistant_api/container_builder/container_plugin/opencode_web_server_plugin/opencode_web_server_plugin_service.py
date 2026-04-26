from __future__ import annotations

from pathlib import PurePosixPath

from assistant_api.models import ContainerRuntimeContext, ContainerSpec, ImageSpec


class OpenCodeWebServerPluginService:
    name = "opencode-web-server"

    def __init__(self, host_port: int = 4096, container_port: int = 4096) -> None:
        self.host_port = host_port
        self.container_port = container_port

    def configure_image(self, image: ImageSpec) -> None:
        return None

    def configure_container(self, container: ContainerSpec) -> None:
        if container.working_dir is None:
            container.working_dir = PurePosixPath("/workspace")
        container.command = [
            "opencode",
            "web",
            "--hostname",
            "0.0.0.0",
            "--port",
            str(self.container_port),
        ]
        container.ports[self.container_port] = self.host_port

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        return None
