from __future__ import annotations

import json
import os
import secrets
import shlex
import subprocess
import sys
import argparse
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from http.server import ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any, Literal

if __package__:
    from ._http_handler import google_drive_mount_handler_class
    from ._login_page import render_login_page, render_mount_success_page
else:
    from _http_handler import google_drive_mount_handler_class
    from _login_page import render_login_page, render_mount_success_page


GOOGLE_DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
RCLONE_DRIVE_FILE_SCOPE = "drive.file"
RCLONE_POLL_INTERVAL = "10m"
RCLONE_VFS_CACHE_MODE = "writes"
RCLONE_VFS_WRITE_BACK = "5s"
REMOTE_WRITE_PROBE_TIMEOUT_SECONDS = 20

GoogleDriveMountState = Literal["unauthenticated", "authenticating", "authenticated", "mounting", "mounted", "error"]


class GoogleDriveMountAuthError(RuntimeError):
    pass


class LocalFolderImportPathError(RuntimeError):
    pass


class LocalFolderImportConflictError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OAuthCredentials:
    client_id: str
    client_secret: str


class GoogleDriveMountAuthServer:
    def __init__(
        self,
        *,
        auth_port: int,
        host_port: int,
        drive_folder_name: str,
        container_path: PurePosixPath,
        remote_name: str,
        mode: str,
        oauth_authorize_url: str,
        oauth_token_url: str,
        drive_api_base_url: str,
        credentials_json: str,
        public_base_url: str | None = None,
        enable_local_folder_import: bool = False,
    ) -> None:
        self.auth_port = auth_port
        self.host_port = host_port
        self.folder_name = drive_folder_name
        self.container_path = container_path
        self.remote_name = remote_name
        self.mode = mode
        self.oauth_authorize_url = oauth_authorize_url
        self.oauth_token_url = oauth_token_url
        self.drive_api_base_url = drive_api_base_url.rstrip("/")
        self.credentials = _credentials_from_json(credentials_json)
        self.public_base_url = public_base_url
        self.enable_local_folder_import = enable_local_folder_import
        self._server: ThreadingHTTPServer | None = None
        self._state: GoogleDriveMountState = "unauthenticated"
        self._message = "Google Drive is not authenticated."
        self._auth_valid = False
        self._mounted = False
        self._oauth_state: str | None = None
        self._token: dict[str, Any] | None = None
        self.remote_folder_id: str | None = None
        self._state_lock = threading.RLock()

    def serve_forever(self, bind_host: str) -> None:
        self._initialize_mount_state()
        self._server = ThreadingHTTPServer((bind_host, self.auth_port), google_drive_mount_handler_class())
        self._server.plugin = self  # type: ignore[attr-defined]
        self._server.serve_forever()

    def start_in_thread(self, bind_host: str) -> None:
        import threading

        self._initialize_mount_state()
        self._server = ThreadingHTTPServer((bind_host, self.auth_port), google_drive_mount_handler_class())
        self._server.plugin = self  # type: ignore[attr-defined]
        thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        thread.start()

    def restore_persisted_mount(self) -> None:
        self._restore_persisted_mount()

    def _initialize_mount_state(self) -> None:
        try:
            self._load_existing_mount_state()
            if self._mounted or self._state == "error":
                return
            self._restore_persisted_mount()
        except GoogleDriveMountAuthError as error:
            self._set_error(str(error))

    def _login(self) -> tuple[int, str, str]:
        return self._login_page_response(mark_authenticating=True)

    def _login_page_response(self, *, mark_authenticating: bool) -> tuple[int, str, str]:
        if self._mounted:
            return (
                200,
                "text/html; charset=utf-8",
                render_mount_success_page(
                    self.folder_name,
                    enable_local_folder_import=self.enable_local_folder_import,
                ),
            )
        if mark_authenticating:
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
        return 200, "text/html; charset=utf-8", render_login_page(authorize_url, self.folder_name)

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
            self.remote_folder_id = folder_id
            self._state = "mounting"
            self._configure_rclone(folder_id)
            self._mount_rclone()
            self._verify_mount_health()
        except Exception as error:
            self._set_error(str(error))
            return 500, "text/plain; charset=utf-8", self._message
        self._state = "mounted"
        self._mounted = True
        self._message = "Google Drive is mounted."
        return (
            200,
            "text/html; charset=utf-8",
            render_mount_success_page(
                self.folder_name,
                enable_local_folder_import=self.enable_local_folder_import,
            ),
        )

    def _logout(self) -> tuple[int, str, str]:
        subprocess.run(["rclone", "unmount", str(self.container_path)], check=False)
        subprocess.run(["rclone", "config", "delete", self.remote_name], check=False)
        self._token = None
        self._auth_valid = False
        self._mounted = False
        self._state = "unauthenticated"
        self._message = "Google Drive is not authenticated."
        self.remote_folder_id = None
        return self._login_page_response(mark_authenticating=False)

    def _status(self) -> tuple[int, str, str]:
        with self._state_lock:
            if self._state == "mounted":
                try:
                    self._verify_status_mount_health()
                except GoogleDriveMountAuthError as error:
                    self._set_error(str(error))
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

    def _import_local_folder(self, content_type: str, body: bytes) -> tuple[int, str, str]:
        if not self.enable_local_folder_import:
            return 404, "text/plain; charset=utf-8", "Local folder import is disabled."
        if not self._mounted or not self._auth_valid:
            return 409, "text/plain; charset=utf-8", "Google Drive must be mounted before import."
        try:
            self._verify_mount_health()
            files = _local_folder_import_files(content_type, body)
            targets = self._preflight_local_folder_import(files)
            for target, content in targets:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
        except LocalFolderImportPathError as error:
            return 400, "text/plain; charset=utf-8", str(error)
        except LocalFolderImportConflictError as error:
            return 409, "text/plain; charset=utf-8", str(error)
        except GoogleDriveMountAuthError as error:
            return 409, "text/plain; charset=utf-8", f"Google Drive mount is not healthy: {error}"
        except OSError as error:
            return 500, "text/plain; charset=utf-8", f"Local folder import failed: {error}"
        return 200, "text/plain; charset=utf-8", "Local folder import completed."

    def _preflight_local_folder_import(
        self,
        files: list[tuple[str, bytes]],
    ) -> list[tuple[Path, bytes]]:
        root = Path(self.container_path)
        root_resolved = root.resolve()
        targets: list[tuple[Path, bytes]] = []
        seen: set[Path] = set()
        for relative_path, content in files:
            target = _safe_local_import_target(root, root_resolved, relative_path)
            if target in seen:
                raise LocalFolderImportConflictError(f"Import path is duplicated: {relative_path}")
            seen.add(target)
            if target.exists():
                raise LocalFolderImportConflictError(
                    f"Import target already exists: {relative_path}"
                )
            targets.append((target, content))
        for target, _content in targets:
            _verify_local_import_parent_paths(root, target, seen)
        return targets

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
        payload = self._json_request(
            urllib.request.Request(
                self.oauth_token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        )
        if not isinstance(payload.get("access_token"), str):
            raise GoogleDriveMountAuthError("OAuth token response did not include access_token.")
        return payload

    def _find_or_create_folder(self, access_token: str) -> str:
        query = urllib.parse.urlencode(
            {
                "q": " and ".join(
                    [
                        f"name = '{self.folder_name}'",
                        "mimeType = 'application/vnd.google-apps.folder'",
                        "trashed = false",
                        "'root' in parents",
                    ]
                ),
                "fields": "files(id,name,mimeType)",
                "spaces": "drive",
            }
        )
        payload = self._drive_request(f"{self.drive_api_base_url}/files?{query}", access_token)
        files = payload.get("files")
        if not isinstance(files, list):
            raise GoogleDriveMountAuthError("Google Drive folder search response did not include files.")
        if files:
            if len(files) > 1:
                raise GoogleDriveMountAuthError(
                    f"Multiple root-level Google Drive folders named '{self.folder_name}' "
                    f"already exist. Remove the duplicates in My Drive and try again."
                )
            folder_id = files[0].get("id")
            if isinstance(folder_id, str) and folder_id:
                return folder_id
            raise GoogleDriveMountAuthError("Google Drive folder search returned a folder without id.")
        created = self._drive_request(
            f"{self.drive_api_base_url}/files",
            access_token,
            data=json.dumps(
                {
                    "name": self.folder_name,
                    "mimeType": "application/vnd.google-apps.folder",
                }
            ).encode("utf-8"),
        )
        folder_id = created.get("id")
        if not isinstance(folder_id, str) or not folder_id:
            raise GoogleDriveMountAuthError("Google Drive folder creation response did not include id.")
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
                raise GoogleDriveMountAuthError(f"HTTP request failed with status {response.status}.")
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise GoogleDriveMountAuthError("HTTP JSON response was not an object.")
        return payload

    def _configure_rclone(self, folder_id: str) -> None:
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
                json.dumps(_rclone_token_from_oauth_token(self._token)),
                "root_folder_id",
                folder_id,
                "--non-interactive",
            ],
            "rclone config failed",
        )

    def _mount_rclone(self) -> None:
        self._exec_checked(
            ["/bin/sh", "-lc", f"mkdir -p {shlex.quote(str(self.container_path))}"],
            "mount target creation failed",
        )
        self._verify_mount_target_empty()
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

    def _verify_mount_target_empty(self) -> None:
        target = Path(self.container_path)
        try:
            next(target.iterdir())
        except StopIteration:
            return
        except FileNotFoundError as error:
            raise GoogleDriveMountAuthError(
                f"Google Drive mount target does not exist after creation: {self.container_path}"
            ) from error
        raise GoogleDriveMountAuthError(
            f"Google Drive mount target must be empty before mount: {self.container_path}"
        )

    def _verify_mountpoint(self) -> None:
        self._exec_checked(["mountpoint", "-q", str(self.container_path)], "mountpoint verification failed")

    def _verify_mounted_path_readable(self) -> None:
        target = Path(self.container_path)
        try:
            list(target.iterdir())
        except OSError as error:
            raise GoogleDriveMountAuthError(
                f"Google Drive mount filesystem read failed: {error}"
            ) from error

    def _verify_remote_readable(self) -> None:
        try:
            self._exec_checked(
                ["rclone", "lsf", f"{self.remote_name}:"],
                "Google Drive remote read verification failed",
            )
        except GoogleDriveMountAuthError as error:
            if not self._force_refresh_persisted_rclone_token(str(error)):
                raise
            self._exec_checked(
                ["rclone", "lsf", f"{self.remote_name}:"],
                "Google Drive remote read verification failed after token refresh",
            )

    def _verify_mount_health(self) -> None:
        self._verify_mountpoint()
        self._verify_remote_readable()
        if self.mode != "ro":
            self._verify_remote_write_through_mount()

    def _verify_status_mount_health(self) -> None:
        self._verify_mountpoint()
        self._verify_mounted_path_readable()
        self._verify_remote_readable()

    def _verify_remote_write_through_mount(self) -> None:
        probe_name = f".notes-assistant-gdrive-upload-health-{os.getpid()}-{secrets.token_hex(8)}"
        probe_path = Path(self.container_path) / probe_name
        remote_path = f"{self.remote_name}:{probe_name}"
        content = "ok"
        try:
            probe_path.write_text(content, encoding="utf-8")
            timeout_seconds = float(
                os.environ.get(
                    "GOOGLE_DRIVE_REMOTE_WRITE_PROBE_TIMEOUT_SECONDS",
                    str(REMOTE_WRITE_PROBE_TIMEOUT_SECONDS),
                )
            )
            deadline = time.monotonic() + timeout_seconds
            last_error = "remote write probe did not run"
            while time.monotonic() < deadline:
                result = subprocess.run(
                    ["rclone", "cat", remote_path],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0 and result.stdout == content:
                    return
                last_error = (result.stderr or result.stdout or "remote content mismatch").strip()
                time.sleep(1)
            raise GoogleDriveMountAuthError(
                "Google Drive remote write verification failed: "
                f"probe file was not readable from remote storage: {last_error}"
            )
        except OSError as error:
            raise GoogleDriveMountAuthError(
                f"Google Drive mount write verification failed: {error}"
            ) from error
        finally:
            subprocess.run(["rclone", "deletefile", remote_path], check=False)
            try:
                probe_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                subprocess.run(["rclone", "cleanup", f"{self.remote_name}:"], check=False)

    def _restore_persisted_mount(self) -> None:
        config_path = _rclone_config_path()
        if config_path.parent.exists() and not config_path.parent.is_dir():
            raise GoogleDriveMountAuthError("persisted rclone config path parent is not a directory")
        if not config_path.exists() or f"[{self.remote_name}]" not in config_path.read_text(encoding="utf-8"):
            return
        self._normalize_persisted_rclone_token(config_path)
        self._state = "mounting"
        self._verify_remote_readable()
        self._mount_rclone()
        self._verify_mount_health()
        self._auth_valid = True
        self._mounted = True
        self._state = "mounted"
        self._message = "Google Drive is mounted."

    def _load_existing_mount_state(self) -> None:
        config_path = _rclone_config_path()
        if config_path.parent.exists() and not config_path.parent.is_dir():
            raise GoogleDriveMountAuthError("persisted rclone config path parent is not a directory")
        if not config_path.exists() or f"[{self.remote_name}]" not in config_path.read_text(encoding="utf-8"):
            return
        self._normalize_persisted_rclone_token(config_path)
        result = subprocess.run(
            ["mountpoint", "-q", str(self.container_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return
        try:
            self._verify_mount_health()
        except GoogleDriveMountAuthError as error:
            self._set_error(str(error))
            return
        self._auth_valid = True
        self._mounted = True
        self._state = "mounted"
        self._message = "Google Drive is mounted."

    def _normalize_persisted_rclone_token(self, config_path: Path) -> None:
        config_text = config_path.read_text(encoding="utf-8")
        normalized = _normalize_rclone_config_token(config_text, self.remote_name)
        if normalized != config_text:
            config_path.write_text(normalized, encoding="utf-8")

    def _force_refresh_persisted_rclone_token(self, error_message: str) -> bool:
        if "Invalid Credentials" not in error_message:
            return False
        config_path = _rclone_config_path()
        if not config_path.exists():
            return False
        config_text = config_path.read_text(encoding="utf-8")
        normalized = _normalize_rclone_config_token(
            config_text,
            self.remote_name,
            force_expired=True,
        )
        if normalized == config_text:
            return False
        config_path.write_text(normalized, encoding="utf-8")
        return True

    def _exec_checked(self, command: list[str], message: str) -> None:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            raise GoogleDriveMountAuthError(f"{message}: {result.stdout}{result.stderr}")

    def _redirect_uri(self) -> str:
        if self.public_base_url:
            return f"{self.public_base_url}/oauth/callback"
        return f"http://127.0.0.1:{self.host_port}/oauth/callback"

    def _set_error(self, message: str) -> None:
        with self._state_lock:
            self._state = "error"
            self._message = message or "Google Drive mount failed."
            self._auth_valid = False
            self._mounted = False


def _credentials_from_json(raw: str) -> OAuthCredentials:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise GoogleDriveMountAuthError("GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON must be valid JSON") from error
    web = payload.get("web") if isinstance(payload, dict) else None
    if not isinstance(web, dict):
        raise GoogleDriveMountAuthError("GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON must describe a Web client")
    client_id = web.get("client_id")
    client_secret = web.get("client_secret")
    if not isinstance(client_id, str) or not client_id:
        raise GoogleDriveMountAuthError("GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON must contain web.client_id")
    if not isinstance(client_secret, str) or not client_secret:
        raise GoogleDriveMountAuthError("GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON must contain web.client_secret")
    return OAuthCredentials(client_id=client_id, client_secret=client_secret)


def _local_folder_import_files(content_type: str, body: bytes) -> list[tuple[str, bytes]]:
    if "multipart/form-data" not in content_type.lower():
        raise LocalFolderImportPathError("Local folder import requires multipart/form-data.")
    message = BytesParser(policy=policy.default).parsebytes(
        b"Content-Type: " + content_type.encode("utf-8") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    )
    if not message.is_multipart():
        raise LocalFolderImportPathError("Local folder import requires multipart file parts.")
    files: list[tuple[str, bytes]] = []
    for part in message.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        if part.get_param("name", header="content-disposition") != "files":
            continue
        filename = part.get_filename()
        if filename is None:
            raise LocalFolderImportPathError("Local folder import file path is missing.")
        payload = part.get_payload(decode=True)
        files.append((filename, payload if payload is not None else b""))
    if not files:
        raise LocalFolderImportPathError("Local folder import requires at least one file path.")
    return files


def _safe_local_import_target(root: Path, root_resolved: Path, relative_path: str) -> Path:
    if not relative_path:
        raise LocalFolderImportPathError("Local folder import path must not be empty.")
    if "\\" in relative_path:
        raise LocalFolderImportPathError(f"Local folder import path is unsafe: {relative_path}")
    pure_path = PurePosixPath(relative_path)
    if pure_path.is_absolute():
        raise LocalFolderImportPathError(f"Local folder import path must be relative: {relative_path}")
    parts = relative_path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise LocalFolderImportPathError(f"Local folder import path is unsafe: {relative_path}")
    target = root.joinpath(*parts)
    try:
        target.resolve(strict=False).relative_to(root_resolved)
    except ValueError as error:
        raise LocalFolderImportPathError(
            f"Local folder import path escapes mounted root: {relative_path}"
        ) from error
    return target


def _verify_local_import_parent_paths(root: Path, target: Path, imported_targets: set[Path]) -> None:
    relative_parent = target.parent.relative_to(root)
    current = root
    for part in relative_parent.parts:
        current = current / part
        if current in imported_targets:
            raise LocalFolderImportConflictError(
                f"Import target already exists as a file path: {current.relative_to(root)}"
            )
        if current.exists() and not current.is_dir():
            raise LocalFolderImportConflictError(
                f"Import target parent already exists as a file: {current.relative_to(root)}"
            )


def _rclone_token_from_oauth_token(token: dict[str, Any] | None) -> dict[str, Any]:
    if token is None:
        raise GoogleDriveMountAuthError("OAuth token has not been initialized.")
    access_token = token.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise GoogleDriveMountAuthError("OAuth token response did not include access_token.")
    rclone_token: dict[str, Any] = {"access_token": access_token}
    token_type = token.get("token_type")
    if isinstance(token_type, str) and token_type:
        rclone_token["token_type"] = token_type
    refresh_token = token.get("refresh_token")
    if isinstance(refresh_token, str) and refresh_token:
        rclone_token["refresh_token"] = refresh_token
    expiry = token.get("expiry")
    if isinstance(expiry, str) and expiry:
        rclone_token["expiry"] = expiry
        return rclone_token
    expires_in = token.get("expires_in")
    if isinstance(expires_in, (int, float)):
        rclone_token["expiry"] = _rclone_expiry_timestamp(expires_in)
    else:
        rclone_token["expiry"] = _expired_rclone_token_timestamp()
    return rclone_token


def _normalize_rclone_config_token(
    config_text: str,
    remote_name: str,
    *,
    force_expired: bool = False,
) -> str:
    lines = config_text.splitlines()
    output: list[str] = []
    in_remote = False
    changed = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_remote = stripped == f"[{remote_name}]"
            output.append(line)
            continue
        if in_remote and stripped.startswith("token = "):
            prefix, raw_token = line.split("=", 1)
            token = json.loads(raw_token.strip())
            if not isinstance(token, dict):
                raise GoogleDriveMountAuthError("persisted rclone token is not a JSON object")
            normalized_token = _rclone_token_from_persisted_token(
                token,
                force_expired=force_expired,
            )
            if normalized_token != token:
                changed = True
                output.append(f"{prefix}= {json.dumps(normalized_token)}")
                continue
        output.append(line)
    trailing_newline = "\n" if config_text.endswith("\n") else ""
    normalized = "\n".join(output) + trailing_newline
    return normalized if changed else config_text


def _rclone_expiry_timestamp(expires_in_seconds: int | float) -> str:
    seconds = max(float(expires_in_seconds) - 60, 0)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return expiry.isoformat(timespec="seconds").replace("+00:00", "Z")


def _expired_rclone_token_timestamp() -> str:
    return "1970-01-01T00:00:00Z"


def _rclone_token_from_persisted_token(
    token: dict[str, Any],
    *,
    force_expired: bool = False,
) -> dict[str, Any]:
    access_token = token.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise GoogleDriveMountAuthError("persisted rclone token did not include access_token")
    rclone_token: dict[str, Any] = {"access_token": access_token}
    token_type = token.get("token_type")
    if isinstance(token_type, str) and token_type:
        rclone_token["token_type"] = token_type
    refresh_token = token.get("refresh_token")
    if isinstance(refresh_token, str) and refresh_token:
        rclone_token["refresh_token"] = refresh_token
    expiry = token.get("expiry")
    if force_expired:
        rclone_token["expiry"] = _expired_rclone_token_timestamp()
    elif isinstance(expiry, str) and expiry:
        rclone_token["expiry"] = expiry
    else:
        rclone_token["expiry"] = _expired_rclone_token_timestamp()
    return rclone_token


def _rclone_config_path() -> Path:
    config = os.environ.get("RCLONE_CONFIG")
    if not config:
        return Path.home() / ".config" / "rclone" / "rclone.conf"
    return Path(config)


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise GoogleDriveMountAuthError(f"{name} is required")
    return value


def _required_port_env(name: str) -> int:
    value = _required_env(name)
    try:
        port = int(value)
    except ValueError as error:
        raise GoogleDriveMountAuthError(f"{name} must be an integer TCP port") from error
    if port < 1 or port > 65535:
        raise GoogleDriveMountAuthError(f"{name} must be an integer TCP port")
    return port


def _optional_bool_env(name: str) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return False
    if value == "1":
        return True
    if value == "0":
        return False
    raise GoogleDriveMountAuthError(f"{name} must be 1 or 0")


def _optional_str_env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return None
    return value


def main() -> None:
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--restore-persisted-mount", action="store_true")
        args = parser.parse_args()
        server = GoogleDriveMountAuthServer(
            auth_port=_required_port_env("GOOGLE_DRIVE_AUTH_PORT"),
            host_port=_required_port_env("GOOGLE_DRIVE_AUTH_HOST_PORT"),
            drive_folder_name=_required_env("GOOGLE_DRIVE_MOUNT_FOLDER_NAME"),
            container_path=PurePosixPath(_required_env("GOOGLE_DRIVE_MOUNT_CONTAINER_PATH")),
            remote_name=_required_env("GOOGLE_DRIVE_REMOTE_NAME"),
            mode=_required_env("GOOGLE_DRIVE_MOUNT_MODE"),
            oauth_authorize_url=_required_env("GOOGLE_DRIVE_OAUTH_AUTHORIZE_URL"),
            oauth_token_url=_required_env("GOOGLE_DRIVE_OAUTH_TOKEN_URL"),
            drive_api_base_url=_required_env("GOOGLE_DRIVE_API_BASE_URL"),
            credentials_json=_required_env("GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON"),
            public_base_url=_optional_str_env("GOOGLE_DRIVE_PUBLIC_BASE_URL"),
            enable_local_folder_import=_optional_bool_env(
                "GOOGLE_DRIVE_ENABLE_LOCAL_FOLDER_IMPORT"
            ),
        )
        if args.restore_persisted_mount:
            server.restore_persisted_mount()
            return
        server.serve_forever("0.0.0.0")
    except GoogleDriveMountAuthError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
