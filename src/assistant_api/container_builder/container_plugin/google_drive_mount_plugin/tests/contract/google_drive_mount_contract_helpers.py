from __future__ import annotations

import html
import json
import os
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any


GOOGLE_DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
FORBIDDEN_SCOPES = {
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.metadata",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
    "https://www.googleapis.com/auth/drive.appdata",
    "https://www.googleapis.com/auth/drive.appfolder",
}
REQUIRED_ENV = (
    "GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON",
)


@dataclass(slots=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    url: str

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")

    def json(self) -> dict[str, Any]:
        return json.loads(self.text)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def service_class() -> type[Any]:
    from assistant_api.container_builder.container_plugin.google_drive_mount_plugin import (
        GoogleDriveMountPluginService,
    )

    return GoogleDriveMountPluginService


def unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def auth_port() -> int:
    return unused_port()


def read_url(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    allow_redirects: bool = True,
) -> HttpResponse:
    request = urllib.request.Request(url, data=data, headers=headers or {})
    opener = urllib.request.build_opener() if allow_redirects else urllib.request.build_opener(
        NoRedirect
    )
    try:
        with opener.open(request, timeout=5) as response:
            return HttpResponse(
                status=response.status,
                headers=dict(response.headers),
                body=response.read(),
                url=response.url,
            )
    except urllib.error.HTTPError as error:
        return HttpResponse(
            status=error.code,
            headers=dict(error.headers),
            body=error.read(),
            url=url,
        )


def service_url(host_port: int, path: str) -> str:
    return f"http://127.0.0.1:{host_port}{path}"


def extract_login_href(page_html: str) -> str:
    match = re.search(r'href=["\']([^"\']+)["\']', page_html)
    assert match is not None, page_html
    return html.unescape(match.group(1))


def complete_oauth_flow(host_port: int) -> HttpResponse:
    login = read_url(service_url(host_port, "/login"))
    assert login.status == 200
    assert "text/html" in login.headers.get("Content-Type", "")
    authorize_url = extract_login_href(login.text)
    authorize_redirect = read_url(authorize_url, allow_redirects=False)
    assert authorize_redirect.status in {302, 303}
    callback_url = authorize_redirect.headers["Location"]
    return read_url(callback_url, allow_redirects=False)


def status_json(host_port: int) -> dict[str, Any]:
    response = read_url(service_url(host_port, "/status"))
    assert response.status == 200
    return response.json()


def assert_error_status(host_port: int) -> None:
    status = status_json(host_port)
    assert status["state"] == "error"
    assert status["authValid"] is False
    assert status["mounted"] is False
    assert status["message"]


def assert_rclone_config_precedes_mount(
    fake_rclone: Any,
    *,
    remote_name: str,
    folder_id: str,
    container_path: PurePosixPath,
) -> None:
    commands = fake_rclone.commands()
    config_indexes = [index for index, command in enumerate(commands) if command[:1] == ["config"]]
    mount_indexes = [index for index, command in enumerate(commands) if command[:1] == ["mount"]]
    assert config_indexes, commands
    assert mount_indexes, commands
    assert min(config_indexes) < min(mount_indexes)
    assert any(remote_name in command and folder_id in command for command in commands)
    assert any(
        command[:1] == ["config"] and "drive.file" in command and "--non-interactive" in command
        for command in commands
    )
    assert any(
        command[:1] == ["mount"]
        and command[1].startswith(f"{remote_name}:")
        and str(container_path) in command
        and "--poll-interval" in command
        and "10m" in command
        and "--vfs-cache-mode" in command
        and "writes" in command
        and "--vfs-write-back" in command
        and "5s" in command
        for command in commands
    )


def require_manual_env() -> None:
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise AssertionError(
            "manual Google Drive mount test requires Doppler env vars: " + ", ".join(missing)
        )
