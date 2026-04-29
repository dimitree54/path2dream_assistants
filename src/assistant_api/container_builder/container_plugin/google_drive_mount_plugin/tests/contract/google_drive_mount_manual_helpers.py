from __future__ import annotations

import json
import os
import re
import socket
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlparse

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin.google_drive_mount_plugin import (
    GoogleDriveMountPluginService,
)
from assistant_api.container_builder.container_plugin.google_drive_persistence_plugin import (
    GoogleDrivePersistencePluginService,
)
from assistant_api.models import RunningContainer
from google_drive_mount_contract_helpers import extract_login_href, read_url, service_url, status_json


MOUNT_PATH = PurePosixPath("/workspace/project")
MANUAL_DRIVE_FOLDER_NAME = "Notes Assistant API Manual Folder"
MANUAL_CONFIG_VOLUME = "notes_assistant_api_google_drive_manual_contract_config"
MANUAL_CACHE_VOLUME = "notes_assistant_api_google_drive_manual_contract_cache"
MANUAL_CONTAINER_NAME = f"notes-assistant-gdrive-manual-contract-{os.getpid()}"


def host_port_from_google_credentials(raw_credentials: str) -> int:
    try:
        payload = json.loads(raw_credentials)
    except json.JSONDecodeError as error:
        raise AssertionError("GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON must be valid JSON.") from error
    web = payload.get("web") if isinstance(payload, dict) else None
    redirect_uris = web.get("redirect_uris") if isinstance(web, dict) else None
    if not isinstance(redirect_uris, list):
        raise AssertionError(
            "GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON.web.redirect_uris is required for manual tests."
        )
    for redirect_uri in redirect_uris:
        if not isinstance(redirect_uri, str):
            continue
        parsed = urlparse(redirect_uri)
        if parsed.scheme != "http":
            continue
        if parsed.hostname != "127.0.0.1":
            continue
        if parsed.path != "/oauth/callback":
            continue
        if parsed.port is None:
            continue
        return int(parsed.port)
    raise AssertionError(
        "manual tests require redirect URI 'http://127.0.0.1:<port>/oauth/callback' "
        "in GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON.web.redirect_uris"
    )


def assert_local_port_available(port: int) -> None:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as error:
            raise AssertionError(
                f"manual test cannot bind host port 127.0.0.1:{port}; "
                "free this port or update OAuth redirect URIs"
            ) from error


@dataclass(slots=True)
class LiveGoogleDriveMountRuntime:
    host_port: int
    container_name: str = MANUAL_CONTAINER_NAME
    config_volume: str = MANUAL_CONFIG_VOLUME
    cache_volume: str = MANUAL_CACHE_VOLUME
    builder: ContainerBuilderService | None = None
    running: RunningContainer | None = None

    def start(self) -> None:
        self.builder = self._builder()
        self.running = self.builder.build_and_run()
        self.wait_for_status_endpoint()

    def stop(self, *, remove: bool) -> None:
        if self.builder is None:
            return
        self.builder.stop(remove=remove)
        self.running = None

    def ensure_mounted(self, *, timeout_seconds: float = 300.0) -> dict[str, object]:
        status = status_json(self.host_port)
        if status.get("mounted") is True and status.get("state") == "mounted":
            return status
        login_response = read_url(service_url(self.host_port, "/login"))
        assert login_response.status == 200, login_response.text
        authorize_url = extract_login_href(login_response.text)
        print(
            "\nOpen this Google OAuth URL to authorize the manual test:\n"
            f"{authorize_url}\n"
        )
        return self.wait_for_mounted_status(timeout_seconds=timeout_seconds)

    def wait_for_mounted_without_login(self, *, timeout_seconds: float = 180.0) -> dict[str, object]:
        deadline = time.monotonic() + timeout_seconds
        last_status: dict[str, object] | None = None
        while time.monotonic() < deadline:
            status = status_json(self.host_port)
            last_status = status
            if status.get("mounted") is True and status.get("state") == "mounted":
                return status
            if status.get("state") == "error":
                raise AssertionError(f"mount moved to error state: {status!r}")
            time.sleep(2)
        raise AssertionError(
            "persisted mount did not restore without login; "
            f"last status: {last_status!r}"
        )

    def wait_for_mounted_status(self, *, timeout_seconds: float) -> dict[str, object]:
        deadline = time.monotonic() + timeout_seconds
        last_status: dict[str, object] | None = None
        while time.monotonic() < deadline:
            status = status_json(self.host_port)
            last_status = status
            if status.get("mounted") is True and status.get("state") == "mounted":
                return status
            if status.get("state") == "error":
                raise AssertionError(f"mount moved to error state: {status!r}")
            time.sleep(2)
        raise AssertionError(f"mount did not become ready: {last_status!r}")

    def wait_for_status_endpoint(self, *, timeout_seconds: float = 60.0) -> dict[str, object]:
        deadline = time.monotonic() + timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                return status_json(self.host_port)
            except Exception as error:
                last_error = error
                time.sleep(1)
        raise AssertionError(f"status endpoint did not become ready: {last_error}")

    def restart_container(self) -> None:
        self.stop(remove=True)
        self.start()

    def logout(self) -> None:
        response = read_url(service_url(self.host_port, "/logout"))
        assert response.status == 200, response.text

    def exec_text(self, command: list[str], *, context: str) -> str:
        if self.running is None:
            raise AssertionError("container is not running")
        result = self.running.container.exec_run(command)
        raw = result.output
        if isinstance(raw, bytes):
            output_text = raw.decode("utf-8", errors="replace")
        else:
            output_text = str(raw)
        if result.exit_code != 0:
            raise AssertionError(
                f"{context} failed with exit code {result.exit_code}: {output_text}"
            )
        return output_text

    def _builder(self) -> ContainerBuilderService:
        return ContainerBuilderService(
            plugins=[
                GoogleDrivePersistencePluginService(
                    config_volume=self.config_volume,
                    cache_volume=self.cache_volume,
                ),
                GoogleDriveMountPluginService(
                    host_port=self.host_port,
                    drive_folder_name=MANUAL_DRIVE_FOLDER_NAME,
                    container_path=MOUNT_PATH,
                ),
            ],
            container_name=self.container_name,
        )


def unique_relative_dir(test_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", test_name.lower()).strip("-")
    timestamp = int(time.time() * 1000)
    return f".manual-gdrive-{slug}-{timestamp}"


def wait_for_remote_file_content(
    runtime: LiveGoogleDriveMountRuntime,
    *,
    relative_path: str,
    expected_content: str,
    timeout_seconds: float = 180.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_output = ""
    while time.monotonic() < deadline:
        if runtime.running is None:
            raise AssertionError("container is not running")
        result = runtime.running.container.exec_run(
            ["rclone", "cat", f"gdrive:{relative_path}"]
        )
        raw = result.output
        output = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        last_output = output
        if result.exit_code == 0 and output == expected_content:
            return
        time.sleep(2)
    raise AssertionError(
        "remote Drive file did not reach expected content; "
        f"path={relative_path!r} last_output={last_output!r}"
    )
