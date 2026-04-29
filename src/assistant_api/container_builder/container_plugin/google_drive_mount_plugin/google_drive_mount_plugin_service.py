from __future__ import annotations

import base64
import os
import shlex
from pathlib import Path, PurePosixPath

from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.container_builder.container_plugin import (
    MOUNT_METADATA_STATE_KEY,
    OPENCODE_RUNTIME_STATE_KEY,
)
from assistant_api.models import (
    ContainerManagedProcess,
    ContainerRuntimeContext,
    ContainerSpec,
    ContainerStartupTask,
    ImageSpec,
    MountMetadata,
    OpenCodeRuntimeMetadata,
)

from ._credentials import credentials_from_env
from ._login_page import LOGO_ASSET_NAME, SHARED_STYLE_ASSET_NAME


AUTH_SERVER_DIR = "/opt/notes-assistant-api/google_drive_mount_plugin"
AUTH_SERVER_PATH = f"{AUTH_SERVER_DIR}/google_drive_mount_auth_server.py"
HTTP_HANDLER_PATH = f"{AUTH_SERVER_DIR}/_http_handler.py"
LOGIN_PAGE_PATH = f"{AUTH_SERVER_DIR}/_login_page.py"
LOGO_ASSET_PATH = f"{AUTH_SERVER_DIR}/assets/{LOGO_ASSET_NAME}"
SHARED_STYLE_ASSET_PATH = f"{AUTH_SERVER_DIR}/assets/{SHARED_STYLE_ASSET_NAME}"


class GoogleDriveMountPluginService:
    name = "google-drive-mount"

    def __init__(
        self,
        host_port: int,
        drive_folder_name: str,
        container_path: PurePosixPath | None = None,
        auth_container_port: int | None = None,
        remote_name: str = "gdrive",
        mode: str = "rw",
        oauth_authorize_url: str = "https://accounts.google.com/o/oauth2/v2/auth",
        oauth_token_url: str = "https://oauth2.googleapis.com/token",
        drive_api_base_url: str = "https://www.googleapis.com/drive/v3",
    ) -> None:
        self.host_port = self._validate_port("host_port", host_port)
        self.auth_container_port = self._validate_port(
            "auth_container_port",
            auth_container_port if auth_container_port is not None else host_port,
        )
        self._explicit_container_path = container_path
        self.container_path = container_path
        self.remote_name = remote_name
        self.mode = mode
        self.oauth_authorize_url = oauth_authorize_url
        self.oauth_token_url = oauth_token_url
        self.drive_api_base_url = drive_api_base_url.rstrip("/")
        if not drive_folder_name:
            raise ConfigurationError("drive_folder_name is required")
        self.folder_name = drive_folder_name
        credentials_from_env()
        self.credentials_json = os.environ["GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON"]

    def configure_image(self, image: ImageSpec) -> None:
        image.run_commands.append("apk add --no-cache rclone fuse3 python3")
        image.run_commands.extend(_install_auth_server_commands())
        if self._explicit_container_path is not None:
            image.run_commands.append(f"mkdir -p {shlex.quote(str(self._explicit_container_path))}")

    def configure_container(self, container: ContainerSpec) -> None:
        container_path = self._explicit_container_path
        if container_path is None:
            opencode_runtime = self._opencode_runtime(container.state)
            container_path = opencode_runtime.working_dir / self.folder_name
        self.container_path = container_path
        container.ports[self.auth_container_port] = self.host_port
        container.env.update(
            {
                "GOOGLE_DRIVE_AUTH_PORT": str(self.auth_container_port),
                "GOOGLE_DRIVE_AUTH_HOST_PORT": str(self.host_port),
                "GOOGLE_DRIVE_MOUNT_FOLDER_NAME": self.folder_name,
                "GOOGLE_DRIVE_MOUNT_CONTAINER_PATH": str(container_path),
                "GOOGLE_DRIVE_REMOTE_NAME": self.remote_name,
                "GOOGLE_DRIVE_OAUTH_AUTHORIZE_URL": self.oauth_authorize_url,
                "GOOGLE_DRIVE_OAUTH_TOKEN_URL": self.oauth_token_url,
                "GOOGLE_DRIVE_API_BASE_URL": self.drive_api_base_url,
                "GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON": self.credentials_json,
            }
        )
        self._append_once(container.devices, "/dev/fuse")
        self._append_once(container.cap_add, "SYS_ADMIN")
        self._append_once(container.security_opt, "apparmor:unconfined")
        container.state[MOUNT_METADATA_STATE_KEY] = MountMetadata(
            host_path=None,
            host_basename=self.folder_name,
            source_key=self.remote_name,
            container_path=container_path,
            mode=self.mode,
            source_type="remote",
            remote_name=self.remote_name,
        )
        if self._requires_blocking_restore(container):
            container.startup_tasks.append(
                ContainerStartupTask(
                    name="google-drive-mount-restore",
                    command=["/bin/sh", "-lc", _auth_server_command("--restore-persisted-mount")],
                )
            )
        container.managed_processes.append(
            ContainerManagedProcess(
                name="google-drive-mount",
                command=["/bin/sh", "-lc", _auth_server_command()],
            )
        )

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        if self.container_path is None:
            raise RuntimeError("Google Drive mount container path was not configured")
        result = runtime.exec(
            [
                "/bin/sh",
                "-lc",
                _auth_status_health_command(
                    auth_container_port=self.auth_container_port,
                    container_path=str(self.container_path),
                    remote_name=self.remote_name,
                    mode=self.mode,
                ),
            ]
        )
        if result.exit_code != 0:
            raise RuntimeError(f"Google Drive mount health check failed: {result.output}")

    @staticmethod
    def _append_once(values: list[str], value: str) -> None:
        if value not in values:
            values.append(value)

    @staticmethod
    def _validate_port(name: str, value: int) -> int:
        if not isinstance(value, int) or value < 1 or value > 65535:
            raise ConfigurationError(f"{name} must be an integer TCP port")
        return value

    @staticmethod
    def _opencode_runtime(state: dict[str, object]) -> OpenCodeRuntimeMetadata:
        metadata = state.get(OPENCODE_RUNTIME_STATE_KEY)
        if not isinstance(metadata, OpenCodeRuntimeMetadata):
            raise ConfigurationError("GoogleDriveMountPluginService requires OpenCode runtime metadata")
        return metadata

    @staticmethod
    def _requires_blocking_restore(container: ContainerSpec) -> bool:
        return bool(container.env.get("RCLONE_CONFIG"))


def _install_auth_server_commands() -> list[str]:
    module_dir = Path(__file__).parent
    files = {
        AUTH_SERVER_PATH: module_dir.joinpath("_auth_server.py").read_bytes(),
        HTTP_HANDLER_PATH: module_dir.joinpath("_http_handler.py").read_bytes(),
        LOGIN_PAGE_PATH: module_dir.joinpath("_login_page.py").read_bytes(),
        LOGO_ASSET_PATH: module_dir.joinpath("assets", LOGO_ASSET_NAME).read_bytes(),
        SHARED_STYLE_ASSET_PATH: module_dir.parent.joinpath(
            "assets", SHARED_STYLE_ASSET_NAME
        ).read_bytes(),
    }
    commands: list[str] = []
    for target_path, content in files.items():
        commands.extend(_install_file_commands(target_path, content))
    return commands


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


def _auth_server_command(*args: str) -> str:
    extra_args = "".join(f" {shlex.quote(arg)}" for arg in args)
    return (
        "GOOGLE_DRIVE_AUTH_PORT=$GOOGLE_DRIVE_AUTH_PORT "
        "GOOGLE_DRIVE_AUTH_HOST_PORT=$GOOGLE_DRIVE_AUTH_HOST_PORT "
        "GOOGLE_DRIVE_MOUNT_FOLDER_NAME=$GOOGLE_DRIVE_MOUNT_FOLDER_NAME "
        "GOOGLE_DRIVE_MOUNT_CONTAINER_PATH=$GOOGLE_DRIVE_MOUNT_CONTAINER_PATH "
        "GOOGLE_DRIVE_REMOTE_NAME=$GOOGLE_DRIVE_REMOTE_NAME "
        "GOOGLE_DRIVE_OAUTH_AUTHORIZE_URL=$GOOGLE_DRIVE_OAUTH_AUTHORIZE_URL "
        "GOOGLE_DRIVE_OAUTH_TOKEN_URL=$GOOGLE_DRIVE_OAUTH_TOKEN_URL "
        "GOOGLE_DRIVE_API_BASE_URL=$GOOGLE_DRIVE_API_BASE_URL "
        "GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON=$GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON "
        f"exec python3 {shlex.quote(AUTH_SERVER_PATH)}{extra_args}"
    )


def _auth_status_health_command(
    *,
    auth_container_port: int,
    container_path: str,
    remote_name: str,
    mode: str,
) -> str:
    return (
        "python3 - <<'PY'\n"
        "import json\n"
        "import os\n"
        "import pathlib\n"
        "import subprocess\n"
        "import sys\n"
        "import time\n"
        "import urllib.error\n"
        "import urllib.request\n"
        f"url = 'http://127.0.0.1:{auth_container_port}/status'\n"
        f"container_path = pathlib.Path({container_path!r})\n"
        f"remote_name = {remote_name!r}\n"
        f"mode = {mode!r}\n"
        "deadline = time.monotonic() + 60\n"
        "last_error = ''\n"
        "\n"
        "def persisted_remote_exists():\n"
        "    config = os.environ.get('RCLONE_CONFIG')\n"
        "    if not config:\n"
        "        return False\n"
        "    path = pathlib.Path(config)\n"
        "    return path.exists() and f'[{remote_name}]' in path.read_text(encoding='utf-8')\n"
        "\n"
        "def fetch_status():\n"
        "    with urllib.request.urlopen(url, timeout=2) as response:\n"
        "        return json.loads(response.read().decode('utf-8'))\n"
        "\n"
        "def require_mount_health():\n"
        "    subprocess.run(['mountpoint', '-q', str(container_path)], check=True)\n"
        "    subprocess.run(['rclone', 'lsf', f'{remote_name}:'], check=True, capture_output=True, text=True)\n"
        "    if mode != 'ro':\n"
        "        probe = container_path / f'.notes-assistant-gdrive-health-{os.getpid()}'\n"
        "        try:\n"
        "            probe.write_text('ok', encoding='utf-8')\n"
        "            if probe.read_text(encoding='utf-8') != 'ok':\n"
        "                raise RuntimeError('mount write probe content mismatch')\n"
        "        finally:\n"
        "            try:\n"
        "                probe.unlink()\n"
        "            except FileNotFoundError:\n"
        "                pass\n"
        "\n"
        "while time.monotonic() < deadline:\n"
        "    try:\n"
        "        payload = fetch_status()\n"
        "    except urllib.error.HTTPError as error:\n"
        "        last_error = error.read().decode('utf-8', errors='replace')\n"
        "        time.sleep(1)\n"
        "        continue\n"
        "    except Exception as error:\n"
        "        last_error = str(error)\n"
        "        time.sleep(1)\n"
        "        continue\n"
        "    if payload.get('state') == 'error':\n"
        "        raise SystemExit(f'Google Drive status is unhealthy: {payload}')\n"
        "    if persisted_remote_exists():\n"
        "        if payload.get('authValid') is True and payload.get('mounted') is True and payload.get('state') == 'mounted':\n"
        "            try:\n"
        "                require_mount_health()\n"
        "            except Exception as error:\n"
        "                last_error = f'Google Drive mount verification failed: {error}'\n"
        "                time.sleep(1)\n"
        "                continue\n"
        "            raise SystemExit(0)\n"
        "        last_error = f'persisted remote exists but mount is not ready: {payload}'\n"
        "    elif isinstance(payload.get('state'), str):\n"
        "        raise SystemExit(0)\n"
        "    else:\n"
        "        last_error = f'/status response has no state: {payload}'\n"
        "    time.sleep(1)\n"
        "raise SystemExit(f'Google Drive status did not become healthy: {last_error}')\n"
        "PY"
    )
