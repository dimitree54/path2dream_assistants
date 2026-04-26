from __future__ import annotations

import json
import os
import secrets
import shlex
import threading
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.container_builder.container_plugin import (
    MOUNT_METADATA_STATE_KEY,
    OPENCODE_RUNTIME_STATE_KEY,
)
from assistant_api.models import (
    ContainerRuntimeContext,
    ContainerSpec,
    ImageSpec,
    MountMetadata,
    OpenCodeRuntimeMetadata,
)

from ._credentials import credentials_from_env
from ._http_handler import google_drive_mount_handler_class


GOOGLE_DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
RCLONE_DRIVE_FILE_SCOPE = "drive.file"
RCLONE_POLL_INTERVAL = "10m"
RCLONE_VFS_CACHE_MODE = "writes"
RCLONE_VFS_WRITE_BACK = "5s"


GoogleDriveMountState = Literal["unauthenticated", "authenticating", "authenticated", "mounting", "mounted", "error"]


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
        self.credentials = credentials_from_env()
        self._runtime: ContainerRuntimeContext | None = None
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._state: GoogleDriveMountState = "unauthenticated"
        self._message = "Google Drive is not authenticated."
        self._auth_valid = False
        self._mounted = False
        self._oauth_state: str | None = None
        self._token: dict[str, Any] | None = None

    def configure_image(self, image: ImageSpec) -> None:
        image.run_commands.append("apk add --no-cache rclone fuse3")
        if self._explicit_container_path is not None:
            image.run_commands.append(f"mkdir -p {shlex.quote(str(self._explicit_container_path))}")

    def configure_container(self, container: ContainerSpec) -> None:
        container_path = self._explicit_container_path
        if container_path is None:
            opencode_runtime = self._opencode_runtime(container.state)
            container_path = opencode_runtime.working_dir / self.folder_name
        self.container_path = container_path
        container.ports[self.auth_container_port] = self.host_port
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

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        self._runtime = runtime
        self._restore_persisted_mount()
        self._server = ThreadingHTTPServer(("127.0.0.1", self.host_port), google_drive_mount_handler_class())
        self._server.plugin = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def _login(self) -> tuple[int, str, str]:
        self._state = "authenticating"
        self._message = "Waiting for Google Drive authorization."
        self._oauth_state = secrets.token_urlsafe(24)
        query = urllib.parse.urlencode(
            {
                "client_id": self.credentials.client_id,
                "redirect_uri": self._redirect_uri(),
                "response_type": "code",
                "scope": GOOGLE_DRIVE_FILE_SCOPE,
                "access_type": "offline",
                "prompt": "consent",
                "state": self._oauth_state,
            }
        )
        authorize_url = f"{self.oauth_authorize_url}?{query}"
        body = (
            "<!doctype html><html><body>"
            f'<a href="{authorize_url}">Authorize Google Drive</a>'
            "</body></html>"
        )
        return 200, "text/html; charset=utf-8", body

    def _oauth_callback(self, query: dict[str, list[str]]) -> tuple[int, str, str]:
        if query.get("error"):
            self._set_error(query["error"][0])
            return 400, "text/plain; charset=utf-8", self._message
        if query.get("state", [None])[0] != self._oauth_state:
            self._set_error("OAuth state mismatch.")
            return 400, "text/plain; charset=utf-8", self._message
        code = query.get("code", [None])[0]
        if not code:
            self._set_error("OAuth callback did not include a code.")
            return 400, "text/plain; charset=utf-8", self._message
        try:
            self._token = self._exchange_code(code)
            self._auth_valid = True
            self._state = "authenticated"
            folder_id = self._find_or_create_folder(self._token["access_token"])
            self._record_folder_id(folder_id)
            self._state = "mounting"
            self._configure_rclone(folder_id)
            self._mount_rclone()
            self._verify_mountpoint()
        except Exception as error:
            self._set_error(str(error))
            return 500, "text/plain; charset=utf-8", self._message
        self._state = "mounted"
        self._mounted = True
        self._message = "Google Drive is mounted."
        return 200, "text/plain; charset=utf-8", self._message

    def _logout(self) -> tuple[int, str, str]:
        if self._runtime is not None:
            self._runtime.exec(["rclone", "unmount", str(self._configured_container_path())])
            self._runtime.exec(["rclone", "config", "delete", self.remote_name])
        self._token = None
        self._auth_valid = False
        self._mounted = False
        self._state = "unauthenticated"
        self._message = "Google Drive is not authenticated."
        self._record_folder_id(None)
        return 200, "text/plain; charset=utf-8", "Logged out."

    def _status(self) -> tuple[int, str, str]:
        return (
            200,
            "application/json",
            json.dumps(
                {
                    "authValid": self._auth_valid,
                    "mounted": self._mounted,
                    "state": self._state,
                    "message": self._message,
                }
            ),
        )

    def _exchange_code(self, code: str) -> dict[str, Any]:
        data = urllib.parse.urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._redirect_uri(),
                "client_id": self.credentials.client_id,
                "client_secret": self.credentials.client_secret,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.oauth_token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        payload = self._json_request(request)
        if not isinstance(payload.get("access_token"), str):
            raise RuntimeError("OAuth token response did not include access_token.")
        return payload

    def _find_or_create_folder(self, access_token: str) -> str:
        query = urllib.parse.urlencode(
            {
                "q": " and ".join(
                    [
                        f"name = '{self.folder_name}'",
                        "mimeType = 'application/vnd.google-apps.folder'",
                        "trashed = false",
                    ]
                ),
                "fields": "files(id,name,mimeType)",
                "spaces": "drive",
            }
        )
        payload = self._drive_request(f"{self.drive_api_base_url}/files?{query}", access_token)
        files = payload.get("files")
        if not isinstance(files, list):
            raise RuntimeError("Google Drive folder search response did not include files.")
        if files:
            folder_id = files[0].get("id")
            if isinstance(folder_id, str) and folder_id:
                return folder_id
            raise RuntimeError("Google Drive folder search returned a folder without id.")
        create_payload = {
            "name": self.folder_name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        created = self._drive_request(
            f"{self.drive_api_base_url}/files",
            access_token,
            data=json.dumps(create_payload).encode("utf-8"),
        )
        folder_id = created.get("id")
        if not isinstance(folder_id, str) or not folder_id:
            raise RuntimeError("Google Drive folder creation response did not include id.")
        return folder_id

    def _drive_request(
        self,
        url: str,
        access_token: str,
        data: bytes | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {access_token}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        return self._json_request(urllib.request.Request(url, data=data, headers=headers))

    def _json_request(self, request: urllib.request.Request) -> dict[str, Any]:
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"HTTP request failed with status {response.status}.")
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("HTTP JSON response was not an object.")
        return payload

    def _configure_rclone(self, folder_id: str) -> None:
        token_json = json.dumps(self._token)
        self._exec_checked(
            [
                "rclone",
                "config",
                "create",
                self.remote_name,
                "drive",
                "client_id",
                self.credentials.client_id,
                "client_secret",
                self.credentials.client_secret,
                "scope",
                RCLONE_DRIVE_FILE_SCOPE,
                "token",
                token_json,
                "root_folder_id",
                folder_id,
                "--non-interactive",
            ],
            "rclone config failed",
        )

    def _mount_rclone(self) -> None:
        container_path = self._configured_container_path()
        self._exec_checked(
            ["/bin/sh", "-lc", f"mkdir -p {shlex.quote(str(container_path))}"],
            "mount target creation failed",
        )
        self._exec_checked(
            [
                "rclone",
                "mount",
                f"{self.remote_name}:",
                str(container_path),
                "--daemon",
                "--poll-interval",
                RCLONE_POLL_INTERVAL,
                "--vfs-cache-mode",
                RCLONE_VFS_CACHE_MODE,
                "--vfs-write-back",
                RCLONE_VFS_WRITE_BACK,
            ],
            "rclone mount failed",
        )

    def _verify_mountpoint(self) -> None:
        self._exec_checked(
            ["mountpoint", "-q", str(self._configured_container_path())],
            "mountpoint verification failed",
        )

    def _restore_persisted_mount(self) -> None:
        config = os.environ.get("RCLONE_CONFIG")
        if not config:
            return
        config_path = Path(config)
        if config_path.parent.exists() and not config_path.parent.is_dir():
            raise RuntimeError("persisted rclone config path parent is not a directory")
        if not config_path.exists() or f"[{self.remote_name}]" not in config_path.read_text(encoding="utf-8"):
            return
        self._state = "mounting"
        self._mount_rclone()
        self._verify_mountpoint()
        self._auth_valid = True
        self._mounted = True
        self._state = "mounted"
        self._message = "Google Drive is mounted."

    def _exec_checked(self, command: list[str], message: str) -> None:
        if self._runtime is None:
            raise RuntimeError("Google Drive mount plugin has not been started.")
        result = self._runtime.exec(command)
        if result.exit_code != 0:
            raise RuntimeError(f"{message}: {result.output}")

    def _record_folder_id(self, folder_id: str | None) -> None:
        if self._runtime is None:
            return
        mount = self._runtime.state.get(MOUNT_METADATA_STATE_KEY)
        if isinstance(mount, MountMetadata):
            mount.remote_folder_id = folder_id

    def _redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.host_port}/oauth/callback"

    def _set_error(self, message: str) -> None:
        self._state = "error"
        self._message = message or "Google Drive mount failed."
        self._auth_valid = False
        self._mounted = False

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

    def _configured_container_path(self) -> PurePosixPath:
        if self.container_path is None:
            raise RuntimeError("Google Drive mount target was not configured.")
        return self.container_path
