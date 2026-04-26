from __future__ import annotations

import json
import threading
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from google_drive_mount_contract_helpers import GOOGLE_DRIVE_FILE_SCOPE, unused_port


@dataclass(slots=True)
class OAuthDriveState:
    expected_folder_name: str
    existing_folder_id: str | None = None
    oauth_denied: bool = False
    token_failure: bool = False
    drive_failure: bool = False
    authorize_queries: list[dict[str, list[str]]] = field(default_factory=list)
    token_requests: list[dict[str, list[str]]] = field(default_factory=list)
    drive_queries: list[dict[str, list[str]]] = field(default_factory=list)
    created_folders: list[dict[str, Any]] = field(default_factory=list)
    created_folder_id: str = "created-folder-id"

    @property
    def folder_id(self) -> str:
        return self.existing_folder_id or self.created_folder_id


@dataclass(slots=True)
class OAuthDriveStub:
    server: ThreadingHTTPServer
    thread: threading.Thread
    state: OAuthDriveState

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    @property
    def authorize_url(self) -> str:
        return f"{self.base_url}/oauth/authorize"

    @property
    def token_url(self) -> str:
        return f"{self.base_url}/oauth/token"

    @property
    def drive_api_base_url(self) -> str:
        return f"{self.base_url}/drive/v3"

    def close(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()


def _make_handler() -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: Any) -> None:
            return None

        def do_GET(self) -> None:
            state: OAuthDriveState = self.server.state  # type: ignore[attr-defined]
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/oauth/authorize":
                state.authorize_queries.append(query)
                redirect_uri = query["redirect_uri"][0]
                callback_params = {"state": query.get("state", [""])[0]}
                if state.oauth_denied:
                    callback_params["error"] = "access_denied"
                else:
                    callback_params["code"] = "oauth-code"
                location = f"{redirect_uri}?{urllib.parse.urlencode(callback_params)}"
                self._send_redirect(location)
                return
            if parsed.path == "/drive/v3/files":
                state.drive_queries.append(query)
                if state.drive_failure:
                    self._send_json(500, {"error": {"message": "drive failure"}})
                    return
                files = []
                if state.existing_folder_id:
                    files.append(
                        {
                            "id": state.existing_folder_id,
                            "name": state.expected_folder_name,
                            "mimeType": "application/vnd.google-apps.folder",
                        }
                    )
                self._send_json(200, {"files": files})
                return
            if parsed.path.startswith("/drive/v3/files/"):
                if state.drive_failure:
                    self._send_json(500, {"error": {"message": "drive failure"}})
                    return
                self._send_json(
                    200,
                    {
                        "id": parsed.path.rsplit("/", 1)[-1],
                        "name": state.expected_folder_name,
                        "mimeType": "application/vnd.google-apps.folder",
                    },
                )
                return
            self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:
            state: OAuthDriveState = self.server.state  # type: ignore[attr-defined]
            parsed = urllib.parse.urlparse(self.path)
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if parsed.path == "/oauth/token":
                state.token_requests.append(urllib.parse.parse_qs(body.decode("utf-8")))
                if state.token_failure:
                    self._send_json(400, {"error": "invalid_grant"})
                    return
                self._send_json(
                    200,
                    {
                        "access_token": "access-token",
                        "refresh_token": "refresh-token",
                        "expires_in": 3600,
                        "token_type": "Bearer",
                        "scope": GOOGLE_DRIVE_FILE_SCOPE,
                    },
                )
                return
            if parsed.path == "/drive/v3/files":
                if state.drive_failure:
                    self._send_json(500, {"error": {"message": "drive failure"}})
                    return
                folder = json.loads(body.decode("utf-8"))
                state.created_folders.append(folder)
                self._send_json(
                    200,
                    {
                        "id": state.created_folder_id,
                        "name": folder.get("name"),
                        "mimeType": folder.get("mimeType"),
                    },
                )
                return
            self._send_json(404, {"error": "not found"})

        def _send_redirect(self, location: str) -> None:
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


@pytest.fixture
def google_env(monkeypatch: pytest.MonkeyPatch) -> str:
    folder_name = "Notes Assistant API Contract Folder"
    monkeypatch.setenv(
        "GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON",
        json.dumps(
            {
                "web": {
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                    "redirect_uris": ["http://127.0.0.1/oauth/callback"],
                }
            }
        ),
    )
    monkeypatch.setenv("GOOGLE_DRIVE_MOUNT_FOLDER_NAME", folder_name)
    monkeypatch.setenv("GOOGLE_DRIVE_AUTH_PORT", str(unused_port()))
    return folder_name


@pytest.fixture
def oauth_drive_stub(google_env: str) -> OAuthDriveStub:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler())
    server.state = OAuthDriveState(expected_folder_name=google_env)  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    stub = OAuthDriveStub(server=server, thread=thread, state=server.state)  # type: ignore[arg-type]
    try:
        yield stub
    finally:
        stub.close()
