from __future__ import annotations

import base64
from pathlib import Path

from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.container_builder.container_plugin import MOUNT_METADATA_STATE_KEY
from assistant_api.models import (
    ContainerManagedProcess,
    ContainerRuntimeContext,
    ContainerSpec,
    ImageSpec,
    MountMetadata,
)

UPLOAD_HANDLER_PATH = "/opt/notes-assistant-api/inbox_upload_handler.py"


class InboxUploadPluginService:
    name = "inbox-upload"

    def __init__(
        self,
        host_port: int = 8090,
        container_port: int | None = None,
        upload_endpoint_path: str = "/api/inbox/upload",
    ) -> None:
        self.host_port = self._validate_port("host_port", host_port)
        self.container_port = self._validate_port(
            "container_port",
            container_port if container_port is not None else host_port,
        )
        self.upload_endpoint_path = self._validate_upload_endpoint_path(
            upload_endpoint_path
        )
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
                    ),
                ],
            )
        )
        container.ports[self.container_port] = self.host_port

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
                    source_type=mount.source_type,
                    remote_name=mount.remote_name,
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
) -> str:
    return (
        f"INBOX_CONTAINER_PATH={container_path!r} "
        f"INBOX_ENDPOINT_PATH={upload_endpoint_path!r} "
        f"INBOX_PORT={container_port} "
        f"exec python3 {UPLOAD_HANDLER_PATH}"
    )


def _upload_health_command(
    *,
    container_path: str,
    container_port: int,
    upload_endpoint_path: str,
    source_type: str,
    remote_name: str | None,
) -> str:
    return (
        "python3 - <<'PY'\n"
        "import json\n"
        "import os\n"
        "import pathlib\n"
        "import subprocess\n"
        "import time\n"
        "import urllib.error\n"
        "import urllib.request\n"
        f"container_path = pathlib.Path({container_path!r})\n"
        f"url = 'http://127.0.0.1:{container_port}{upload_endpoint_path}'\n"
        f"source_type = {source_type!r}\n"
        f"remote_name = {remote_name!r}\n"
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
        "        if source_type == 'remote':\n"
        "            subprocess.run(['mountpoint', '-q', str(container_path)], check=True)\n"
        "            if remote_name:\n"
        "                subprocess.run(['rclone', 'lsf', f'{remote_name}:'], check=True, capture_output=True, text=True)\n"
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
    )
