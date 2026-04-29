from __future__ import annotations

from pathlib import PurePosixPath

from assistant_api.container_builder.container_plugin import OPENCODE_RUNTIME_STATE_KEY
from assistant_api.models import ContainerRuntimeContext, ContainerSpec, ImageSpec
from assistant_api.models import OpenCodeRuntimeMetadata


class OpenCodeServerPluginService:
    name = "opencode-server"

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
            "serve",
            "--hostname",
            "0.0.0.0",
            "--port",
            str(self.container_port),
        ]
        container.ports[self.container_port] = self.host_port
        container.state[OPENCODE_RUNTIME_STATE_KEY] = OpenCodeRuntimeMetadata(
            working_dir=container.working_dir,
            api_container_port=self.container_port,
        )

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        result = runtime.exec(
            [
                "/bin/sh",
                "-lc",
                _opencode_health_command(self.container_port),
            ]
        )
        if result.exit_code != 0:
            raise RuntimeError(f"OpenCode server health check failed: {result.output}")


def _opencode_health_command(container_port: int) -> str:
    return "\n".join(
        [
            "set -eu",
            "attempts=0",
            "while true; do",
            (
                "  if wget -qO- "
                f"http://127.0.0.1:{container_port}/global/health "
                "| grep -q '\"healthy\"[[:space:]]*:[[:space:]]*true'; then"
            ),
            "    exit 0",
            "  fi",
            "  attempts=$((attempts + 1))",
            "  if [ \"$attempts\" -ge 120 ]; then",
            "    printf '%s\\n' 'OpenCode server did not become healthy' >&2",
            "    exit 1",
            "  fi",
            "  sleep 1",
            "done",
        ]
    )
