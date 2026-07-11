from __future__ import annotations

import base64
import shlex
from pathlib import Path
from pathlib import PurePosixPath

from assistant_api.container_builder.container_plugin import OPENCODE_RUNTIME_STATE_KEY
from assistant_api.models import (
    ContainerRuntimeContext,
    ContainerSpec,
    ImageSpec,
    OpenCodeRuntimeMetadata,
    PublishedPort,
)


class OpenCodeServerPluginService:
    name = "opencode-server"
    _opencode_image = "ghcr.io/anomalyco/opencode:1.17.15"

    def __init__(
        self,
        host_port: int = 4096,
        container_port: int = 4096,
        wait_for_mount: bool = False,
        host: str | None = None,
        max_retries: int = 5,
    ) -> None:
        self.host_port = host_port
        self.container_port = container_port
        self.wait_for_mount = wait_for_mount
        self.host = _validate_host(host)
        self.max_retries = _validate_max_retries(max_retries)
        self._working_dir: PurePosixPath | None = None

    def configure_image(self, image: ImageSpec) -> None:
        image.base_image = self._opencode_image
        image.apk_packages.append("python3")
        patch_source = Path(__file__).with_name("_retry_patch.py").read_bytes()
        encoded = base64.b64encode(patch_source).decode("ascii")
        image.run_commands.append(
            "OPENCODE_RETRY_PATCH=1 python3 -c "
            + shlex.quote(
                "import base64;exec(base64.b64decode(" + repr(encoded) + "))"
            )
            + " --binary /usr/local/bin/opencode"
            + f" --max-retries {self.max_retries}"
        )
        return None

    def configure_container(self, container: ContainerSpec) -> None:
        if container.working_dir is None:
            container.working_dir = PurePosixPath("/workspace")
        self._working_dir = container.working_dir
        command = [
            "opencode",
            "serve",
            "--hostname",
            "0.0.0.0",
            "--port",
            str(self.container_port),
        ]
        container.command = _mount_gated_command(
            container.working_dir,
            self.wait_for_mount,
            command,
        )
        container.ports[self.container_port] = _published_port(self.host_port, self.host)
        container.state[OPENCODE_RUNTIME_STATE_KEY] = OpenCodeRuntimeMetadata(
            working_dir=container.working_dir,
            api_container_port=self.container_port,
        )

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        working_dir = self._runtime_working_dir(runtime)
        result = runtime.exec(
            [
                "/bin/sh",
                "-lc",
                _opencode_health_command(
                    container_port=self.container_port,
                    working_dir=working_dir,
                    wait_for_mount=self.wait_for_mount,
                ),
            ]
        )
        if result.exit_code != 0:
            raise RuntimeError(f"OpenCode server health check failed: {result.output}")

    def _runtime_working_dir(self, runtime: ContainerRuntimeContext) -> PurePosixPath:
        metadata = runtime.state.get(OPENCODE_RUNTIME_STATE_KEY)
        if isinstance(metadata, OpenCodeRuntimeMetadata):
            return metadata.working_dir
        if self._working_dir is not None:
            return self._working_dir
        return PurePosixPath("/workspace")


def _mount_gated_command(
    working_dir: PurePosixPath,
    wait_for_mount: bool,
    command: list[str],
) -> list[str]:
    return [
        "/bin/sh",
        "-lc",
        "\n".join(
            [
                *_mount_gate_lines(str(working_dir), wait_for_mount),
                "exec " + shlex.join(command),
            ]
        ),
    ]


def _published_port(host_port: int, host: str | None) -> int | PublishedPort:
    if host is None:
        return host_port
    return PublishedPort(host_port=host_port, host=host)


def _validate_host(value: str | None) -> str | None:
    if value is None:
        return None
    PublishedPort(host_port=1, host=value)
    return value


def _validate_max_retries(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("max_retries must be an integer between 0 and 9")
    if not 0 <= value <= 9:
        raise ValueError("max_retries must be an integer between 0 and 9")
    return value


def _mount_gate_lines(container_path: str, wait_for_mount: bool) -> list[str]:
    if wait_for_mount:
        return [
            "set -eu",
            f"mount_path={shlex.quote(container_path)}",
            "attempts=0",
            'while ! mountpoint -q "$mount_path"; do',
            '  if [ "$attempts" -eq 0 ] || [ $((attempts % 30)) -eq 0 ]; then',
            '    printf "Waiting for mounted path: %s\\n" "$mount_path" >&2',
            "  fi",
            "  attempts=$((attempts + 1))",
            "  sleep 1",
            "done",
        ]
    return [
        "set -eu",
        f"mount_path={shlex.quote(container_path)}",
        'if ! mountpoint -q "$mount_path"; then',
        '  printf "Required mount is not ready: %s\\n" "$mount_path" >&2',
        "  exit 1",
        "fi",
    ]


def _opencode_health_command(
    *,
    container_port: int,
    working_dir: PurePosixPath,
    wait_for_mount: bool,
) -> str:
    return "\n".join(
        [
            *_mount_gate_lines(str(working_dir), wait_for_mount),
            "attempts=0",
            "while true; do",
            (
                "  health_response=$(wget -q -T 5 -O - "
                f"http://127.0.0.1:{container_port}/global/health "
                "2>/dev/null || true)"
            ),
            '  case "$health_response" in',
            '    *\'"healthy":true\'*|*\'"healthy": true\'*) exit 0 ;;',
            "  esac",
            "  attempts=$((attempts + 1))",
            "  if [ \"$attempts\" -ge 120 ]; then",
            "    printf '%s\\n' 'OpenCode server did not become healthy' >&2",
            "    exit 1",
            "  fi",
            "  sleep 1",
            "done",
        ]
    )
