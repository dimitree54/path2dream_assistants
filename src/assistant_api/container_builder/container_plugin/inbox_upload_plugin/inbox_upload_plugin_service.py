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

UPLOAD_HANDLER_PATH = "/opt/notes-assistant-api/inbox_upload_handler.py"


class InboxUploadPluginService:
    name = "inbox-upload"

    def __init__(
        self,
        host_port: int = 8090,
        container_port: int | None = None,
        upload_endpoint_path: str = "/api/inbox/upload",
        wait_for_mount: bool = False,
        host: str | None = None,
    ) -> None:
        self.host_port = self._validate_port("host_port", host_port)
        self.container_port = self._validate_port(
            "container_port",
            container_port if container_port is not None else host_port,
        )
        self.upload_endpoint_path = self._validate_upload_endpoint_path(
            upload_endpoint_path
        )
        self.wait_for_mount = wait_for_mount
        self.host = self._validate_host(host)
        self._container_path: str | None = None

    def configure_image(self, image: ImageSpec) -> None:
        image.apk_packages.extend(["python3", "py3-pip"])
        image.python_packages.extend(["fastapi", "uvicorn", "python-multipart"])
        image.run_commands.extend(_install_upload_handler_commands())

    def configure_container(self, container: ContainerSpec) -> None:
        mount = self._mount_metadata(container.state)
        container_path = str(mount.container_path)
        self._container_path = container_path
        container.managed_processes.append(
            ContainerManagedProcess(
                name="inbox-upload-server",
                command=[
                    "/bin/sh",
                    "-lc",
                    _upload_server_command(
                        container_path=container_path,
                        container_port=self.container_port,
                        upload_endpoint_path=self.upload_endpoint_path,
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
                _upload_health_command(
                    container_path=container_path,
                    container_port=self.container_port,
                    upload_endpoint_path=self.upload_endpoint_path,
                    wait_for_mount=self.wait_for_mount,
                ),
            ]
        )
        if result.exit_code != 0:
            raise RuntimeError(f"inbox upload health check failed: {result.output}")

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
    def _validate_upload_endpoint_path(value: str) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or not value.startswith("/")
            or value == "/"
        ):
            raise ConfigurationError(
                "upload_endpoint_path must start with / and not be just /"
            )
        return value

    @staticmethod
    def _mount_metadata(state: dict[str, object]) -> MountMetadata:
        metadata = state.get(MOUNT_METADATA_STATE_KEY)
        if not isinstance(metadata, MountMetadata):
            raise ConfigurationError(
                "InboxUploadPluginService requires mount metadata"
            )
        return metadata


def _install_upload_handler_commands() -> list[str]:
    module_dir = Path(__file__).parent
    handler_content = module_dir.joinpath("_upload_handler.py").read_bytes()
    return _install_file_commands(UPLOAD_HANDLER_PATH, handler_content)


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


def _upload_server_command(
    container_path: str,
    container_port: int,
    upload_endpoint_path: str,
    wait_for_mount: bool,
) -> str:
    return "\n".join(
        [
            *_mount_gate_lines(container_path, wait_for_mount),
            (
                f"INBOX_CONTAINER_PATH={container_path!r} "
                f"INBOX_ENDPOINT_PATH={upload_endpoint_path!r} "
                f"INBOX_PORT={container_port} "
                f"exec python3 {UPLOAD_HANDLER_PATH}"
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


def _upload_health_command(
    *,
    container_path: str,
    container_port: int,
    upload_endpoint_path: str,
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
        "import urllib.request\n"
        f"container_path = pathlib.Path({container_path!r})\n"
        f"url = 'http://127.0.0.1:{container_port}{upload_endpoint_path}'\n"
        "filename = f'.notes-assistant-inbox-health-{os.getpid()}.txt'\n"
        "content = b'ok'\n"
        "boundary = 'NotesAssistantHealthBoundary'\n"
        "body = b'\\r\\n'.join([\n"
        "    f'--{boundary}'.encode(),\n"
        "    f'Content-Disposition: form-data; name=\"file\"; filename=\"{filename}\"'.encode(),\n"
        "    b'Content-Type: text/plain',\n"
        "    b'',\n"
        "    content,\n"
        "    f'--{boundary}--'.encode(),\n"
        "])\n"
        "deadline = time.monotonic() + 60\n"
        "last_error = ''\n"
        "while time.monotonic() < deadline:\n"
        "    request = urllib.request.Request(\n"
        "        url,\n"
        "        data=body,\n"
        "        method='POST',\n"
        "        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'},\n"
        "    )\n"
        "    try:\n"
        "        with urllib.request.urlopen(request, timeout=2) as response:\n"
        "            response_body = response.read().decode('utf-8')\n"
        "            if response.status != 200:\n"
        "                raise RuntimeError(f'HTTP {response.status}: {response_body}')\n"
        "            payload = json.loads(response_body)\n"
        "            saved_path = pathlib.Path(payload['path'])\n"
        "            expected_path = container_path / 'inbox' / filename\n"
        "            if saved_path != expected_path:\n"
        "                raise RuntimeError(f'unexpected upload path: {saved_path}')\n"
        "            if expected_path.read_bytes() != content:\n"
        "                raise RuntimeError('uploaded probe content mismatch')\n"
        "            expected_path.unlink()\n"
        "            raise SystemExit(0)\n"
        "    except urllib.error.HTTPError as error:\n"
        "        last_error = error.read().decode('utf-8', errors='replace')\n"
        "    except Exception as error:\n"
        "        last_error = str(error)\n"
        "    time.sleep(1)\n"
        "raise SystemExit(f'inbox upload endpoint did not become healthy: {last_error}')\n"
        "PY"
            ),
        ]
    )
