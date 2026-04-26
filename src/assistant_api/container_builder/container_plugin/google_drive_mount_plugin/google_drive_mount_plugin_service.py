from __future__ import annotations

import json
import os
import secrets
import shlex
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import PurePosixPath
from typing import Any, Literal

from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.container_builder.container_plugin import MOUNT_METADATA_STATE_KEY
from assistant_api.models import ContainerRuntimeContext, ContainerSpec, ImageSpec, MountMetadata


GOOGLE_DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
RCLONE_DRIVE_FILE_SCOPE = "drive.file"
RCLONE_POLL_INTERVAL = "10m"
RCLONE_VFS_CACHE_MODE = "writes"
RCLONE_VFS_WRITE_BACK = "5s"


GoogleDriveMountState = Literal[
    "unauthenticated",
    "authenticating",
    "authenticated",
    "mounting",
    "mounted",
    "error",
]


@dataclass(slots=True)
class _OAuthCredentials:
    client_id: str
    client_secret: str


class GoogleDriveMountPluginService:
    name = "google-drive-mount"

    def __init__(
        self,
        container_path: PurePosixPath = PurePosixPath("/workspace/project"),
        remote_name: str = "gdrive",
        mode: str = "rw",
        oauth_authorize_url: str = "https://accounts.google.com/o/oauth2/v2/auth",
        oauth_token_url: str = "https://oauth2.googleapis.com/token",
        drive_api_base_url: str = "https://www.googleapis.com/drive/v3",
    ) -> None:
        self.container_path = container_path
        self.remote_name = remote_name
        self.mode = mode
        self.oauth_authorize_url = oauth_authorize_url
        self.oauth_token_url = oauth_token_url
        self.drive_api_base_url = drive_api_base_url.rstrip("/")
        self.auth_port = self._required_port_env("GOOGLE_DRIVE_AUTH_PORT")
        self.folder_name = self._required_env("GOOGLE_DRIVE_MOUNT_FOLDER_NAME")
        self.credentials = self._credentials_from_env()
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
        image.run_commands.append(f"mkdir -p {shlex.quote(str(self.container_path))}")

    def configure_container(self, container: ContainerSpec) -> None:
        container.ports[self.auth_port] = self.auth_port
        self._append_once(container.devices, "/dev/fuse")
        self._append_once(container.cap_add, "SYS_ADMIN")
        self._append_once(container.security_opt, "apparmor:unconfined")
        container.state[MOUNT_METADATA_STATE_KEY] = MountMetadata(
            host_path=None,
            host_basename=self.folder_name,
            source_key=self.remote_name,
            container_path=self.container_path,
            mode=self.mode,
            source_type="remote",
            remote_name=self.remote_name,
        )

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        self._runtime = runtime
        self._server = ThreadingHTTPServer(("127.0.0.1", self.auth_port), self._handler_class())
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
            self._runtime.exec(["rclone", "unmount", str(self.container_path)])
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
        self._exec_checked(
            [
                "rclone",
                "mount",
                f"{self.remote_name}:",
                str(self.container_path),
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
        self._exec_checked(["mountpoint", "-q", str(self.container_path)], "mountpoint verification failed")

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
        return f"http://127.0.0.1:{self.auth_port}/oauth/callback"

    def _set_error(self, message: str) -> None:
        self._state = "error"
        self._message = message or "Google Drive mount failed."
        self._auth_valid = False
        self._mounted = False

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format: str, *_args: Any) -> None:
                return None

            def do_GET(self) -> None:
                plugin: GoogleDriveMountPluginService = self.server.plugin  # type: ignore[attr-defined]
                parsed = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                if parsed.path == "/login":
                    self._send(*plugin._login())
                    return
                if parsed.path == "/oauth/callback":
                    self._send(*plugin._oauth_callback(query))
                    return
                if parsed.path == "/logout":
                    self._send(*plugin._logout())
                    return
                if parsed.path == "/status":
                    self._send(*plugin._status())
                    return
                self._send(404, "text/plain; charset=utf-8", "Not found.")

            def _send(self, status: int, content_type: str, body: str) -> None:
                payload = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        return Handler

    @staticmethod
    def _append_once(values: list[str], value: str) -> None:
        if value not in values:
            values.append(value)

    @staticmethod
    def _required_env(name: str) -> str:
        value = os.environ.get(name)
        if not value:
            raise ConfigurationError(f"{name} is required")
        return value

    @classmethod
    def _required_port_env(cls, name: str) -> int:
        value = cls._required_env(name)
        try:
            port = int(value)
        except ValueError as error:
            raise ConfigurationError(f"{name} must be an integer TCP port") from error
        if port < 1 or port > 65535:
            raise ConfigurationError(f"{name} must be an integer TCP port")
        return port

    @classmethod
    def _credentials_from_env(cls) -> _OAuthCredentials:
        raw = cls._required_env("GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ConfigurationError("GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON must be valid JSON") from error
        web = payload.get("web") if isinstance(payload, dict) else None
        if not isinstance(web, dict):
            raise ConfigurationError("GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON must describe a Web client")
        client_id = web.get("client_id")
        client_secret = web.get("client_secret")
        if not isinstance(client_id, str) or not client_id:
            raise ConfigurationError("GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON must contain web.client_id")
        if not isinstance(client_secret, str) or not client_secret:
            raise ConfigurationError("GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON must contain web.client_secret")
        return _OAuthCredentials(client_id=client_id, client_secret=client_secret)
