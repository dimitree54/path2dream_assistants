from __future__ import annotations

import base64
import shlex
from pathlib import Path

from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.container_builder.container_plugin import MOUNT_METADATA_STATE_KEY
from assistant_api.models import (
    ContainerManagedProcess,
    ContainerRuntimeContext,
    ContainerSpec,
    ImageSpec,
    MountMetadata,
    PublishedPort,
)

OUTBOX_HANDLER_PATH = "/opt/notes-assistant-api/outbox_download_handler.py"


class OutboxDownloadPluginService:
    name = "outbox-download"

    def __init__(
        self,
        host_port: int = 8090,
        container_port: int | None = None,
        list_endpoint_path: str = "/api/outbox/list",
        download_endpoint_path: str = "/api/outbox/download",
        wait_for_mount: bool = False,
        host: str | None = None,
    ) -> None:
        self.host_port = self._validate_port("host_port", host_port)
        self.container_port = self._validate_port(
            "container_port",
            container_port if container_port is not None else host_port,
        )
        self.list_endpoint_path = self._validate_endpoint_path(
            "list_endpoint_path", list_endpoint_path
        )
        self.download_endpoint_path = self._validate_endpoint_path(
            "download_endpoint_path", download_endpoint_path
        )
        self.wait_for_mount = wait_for_mount
        self.host = self._validate_host(host)
        self._container_path: str | None = None

    def configure_image(self, image: ImageSpec) -> None:
        image.apk_packages.extend(["python3", "py3-pip"])
        image.python_packages.extend(["fastapi", "uvicorn"])
        image.run_commands.extend(_install_outbox_handler_commands())

    def configure_container(self, container: ContainerSpec) -> None:
        mount = self._mount_metadata(container.state)
        container_path = str(mount.container_path)
        self._container_path = container_path
        container.managed_processes.append(
            ContainerManagedProcess(
                name="outbox-download-server",
                command=[
                    "/bin/sh",
                    "-lc",
                    _outbox_server_command(
                        container_path=container_path,
                        container_port=self.container_port,
                        list_endpoint_path=self.list_endpoint_path,
                        download_endpoint_path=self.download_endpoint_path,
                        wait_for_mount=self.wait_for_mount,
                    ),
                ],
            )
        )
        container.ports[self.container_port] = self._published_port()

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        mount = self._mount_metadata(runtime.state)
        container_path = self._container_path or str(mount.container_path)
        result = runtime.exec(
            [
                "/bin/sh",
                "-lc",
                _outbox_health_command(
                    container_path=container_path,
                    container_port=self.container_port,
                    list_endpoint_path=self.list_endpoint_path,
                    download_endpoint_path=self.download_endpoint_path,
                    wait_for_mount=self.wait_for_mount,
                ),
            ]
        )
        if result.exit_code != 0:
            raise RuntimeError(f"outbox download health check failed: {result.output}")

    @staticmethod
    def _validate_port(name: str, value: int) -> int:
        if not isinstance(value, int) or value < 1 or value > 65535:
            raise ConfigurationError(f"{name} must be an integer TCP port")
        return value

    def _published_port(self) -> int | PublishedPort:
        if self.host is None:
            return self.host_port
        return PublishedPort(host_port=self.host_port, host=self.host)

    @staticmethod
    def _validate_host(value: str | None) -> str | None:
        if value is None:
            return None
        try:
            PublishedPort(host_port=1, host=value)
        except ValueError as error:
            raise ConfigurationError("host must be an IP address literal") from error
        return value

    @staticmethod
    def _validate_endpoint_path(name: str, value: str) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or not value.startswith("/")
            or value == "/"
        ):
            raise ConfigurationError(
                f"{name} must start with / and not be just /"
            )
        return value

    @staticmethod
    def _mount_metadata(state: dict[str, object]) -> MountMetadata:
        metadata = state.get(MOUNT_METADATA_STATE_KEY)
        if not isinstance(metadata, MountMetadata):
            raise ConfigurationError(
                "OutboxDownloadPluginService requires mount metadata"
            )
        return metadata


def _install_outbox_handler_commands() -> list[str]:
    module_dir = Path(__file__).parent
    handler_content = module_dir.joinpath("_outbox_handler.py").read_bytes()
    return _install_file_commands(OUTBOX_HANDLER_PATH, handler_content)


def _install_file_commands(target_path: str, content: bytes) -> list[str]:
    encoded = base64.b64encode(content).decode("ascii")
    commands = [
        "python3 -c "
        + repr(
            "import pathlib; "
            f"target = pathlib.Path({target_path!r}); "
            "target.parent.mkdir(parents=True, exist_ok=True); "
            "target.write_bytes(b'')"
        )
    ]
    for index in range(0, len(encoded), 48_000):
        chunk = encoded[index : index + 48_000]
        commands.append(
            "python3 -c "
            + repr(
                "import base64, pathlib; "
                f"pathlib.Path({target_path!r}).open('ab').write("
                f"base64.b64decode({chunk!r}))"
            )
        )
    return commands


def _outbox_server_command(
    container_path: str,
    container_port: int,
    list_endpoint_path: str,
    download_endpoint_path: str,
    wait_for_mount: bool,
) -> str:
    return "\n".join(
        [
            *_mount_gate_lines(container_path, wait_for_mount),
            (
                f"OUTBOX_CONTAINER_PATH={container_path!r} "
                f"OUTBOX_LIST_ENDPOINT_PATH={list_endpoint_path!r} "
                f"OUTBOX_DOWNLOAD_ENDPOINT_PATH={download_endpoint_path!r} "
                f"OUTBOX_PORT={container_port} "
                f"exec python3 {OUTBOX_HANDLER_PATH}"
            ),
        ]
    )


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


def _outbox_health_command(
    *,
    container_path: str,
    container_port: int,
    list_endpoint_path: str,
    download_endpoint_path: str,
    wait_for_mount: bool,
) -> str:
    return "\n".join(
        [
            *_mount_gate_lines(container_path, wait_for_mount),
            (
        "python3 - <<'PY'\n"
        "import json\n"
        "import os\n"
        "import pathlib\n"
        "import time\n"
        "import urllib.error\n"
        "import urllib.parse\n"
        "import urllib.request\n"
        f"container_path = pathlib.Path({container_path!r})\n"
        f"list_url = 'http://127.0.0.1:{container_port}{list_endpoint_path}'\n"
        f"download_base_url = 'http://127.0.0.1:{container_port}{download_endpoint_path}'\n"
        "filename = f'.notes-assistant-outbox-health-{os.getpid()}.txt'\n"
        "content = b'ok'\n"
        "outbox_dir = container_path / 'outbox'\n"
        "deadline = time.monotonic() + 60\n"
        "last_error = ''\n"
        "while time.monotonic() < deadline:\n"
        "    try:\n"
        "        outbox_dir.mkdir(parents=True, exist_ok=True)\n"
        "        probe = outbox_dir / filename\n"
        "        probe.write_bytes(content)\n"
        "        with urllib.request.urlopen(list_url, timeout=2) as response:\n"
        "            listed = json.loads(response.read().decode('utf-8'))\n"
        "        if filename not in listed:\n"
        "            raise RuntimeError(f'probe file missing from list endpoint: {listed}')\n"
        "        download_url = download_base_url + '/' + urllib.parse.quote(filename, safe='')\n"
        "        with urllib.request.urlopen(download_url, timeout=2) as response:\n"
        "            downloaded = response.read()\n"
        "        if downloaded != content:\n"
        "            raise RuntimeError('downloaded probe content mismatch')\n"
        "        delete_deadline = time.monotonic() + 15\n"
        "        while probe.exists() and time.monotonic() < delete_deadline:\n"
        "            time.sleep(0.2)\n"
        "        if probe.exists():\n"
        "            raise RuntimeError('download endpoint did not remove probe file')\n"
        "        raise SystemExit(0)\n"
        "    except urllib.error.HTTPError as error:\n"
        "        last_error = error.read().decode('utf-8', errors='replace')\n"
        "    except Exception as error:\n"
        "        last_error = str(error)\n"
        "        try:\n"
        "            (outbox_dir / filename).unlink()\n"
        "        except FileNotFoundError:\n"
        "            pass\n"
        "    time.sleep(1)\n"
        "raise SystemExit(f'outbox download endpoint did not become healthy: {last_error}')\n"
        "PY"
            ),
        ]
    )
