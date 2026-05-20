from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from assistant_api.models import ContainerRuntimeContext, ContainerSpec


_started_auth_servers: list[object] = []


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


@dataclass(slots=True)
class FakePersistentRclone:
    log_path: Path
    mount_marker: Path

    def commands(self) -> list[list[str]]:
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()]


class ExecRunResult:
    def __init__(self, exit_code: int, output: str = "") -> None:
        self.exit_code = exit_code
        self.output = output.encode("utf-8")


class FakeContainer:
    def __init__(self, mount_marker: Path) -> None:
        self.mount_marker = mount_marker
        self.commands: list[list[str]] = []

    def exec_run(self, command: list[str]) -> ExecRunResult:
        self.commands.append(command)
        if command[:2] == ["/bin/sh", "-lc"]:
            result = subprocess.run(command, check=False, capture_output=True, text=True)
            return ExecRunResult(result.returncode, result.stdout + result.stderr)
        if command[:2] == ["mountpoint", "-q"]:
            return ExecRunResult(0 if self.mount_marker.exists() else 1, "not mounted")
        if command and command[0] == "rclone":
            result = subprocess.run(command, check=False, capture_output=True, text=True)
            return ExecRunResult(result.returncode, result.stdout + result.stderr)
        return ExecRunResult(0)


def persistence_service_class() -> type[Any]:
    from assistant_api.container_builder.container_plugin.google_drive_persistence_plugin import (
        GoogleDrivePersistencePluginService,
    )

    return GoogleDrivePersistencePluginService


def mount_service_class() -> type[Any]:
    from assistant_api.container_builder.container_plugin.google_drive_mount_plugin import (
        GoogleDriveMountPluginService,
    )

    return GoogleDriveMountPluginService


def start_mount_plugin(
    plugin: object,
    container_spec: ContainerSpec,
    state: dict[str, object],
    fake_rclone: FakePersistentRclone,
) -> ContainerRuntimeContext:
    from assistant_api.container_builder.container_plugin.google_drive_mount_plugin._auth_server import (
        __file__ as auth_server_file,
        GoogleDriveMountAuthServer,
    )
    from assistant_api.container_builder.container_plugin.google_drive_mount_plugin.google_drive_mount_plugin_service import (
        AUTH_SERVER_PATH,
    )

    runtime = ContainerRuntimeContext(
        docker_client=object(),
        container=FakeContainer(fake_rclone.mount_marker),
        state=state,
    )
    for task in container_spec.startup_tasks:
        command = list(task.command)
        if command[:2] == ["/bin/sh", "-lc"]:
            command[2] = command[2].replace(AUTH_SERVER_PATH, auth_server_file)
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stdout + result.stderr)
    server = GoogleDriveMountAuthServer(
        auth_port=plugin.auth_container_port,
        host_port=plugin.host_port,
        drive_folder_name=plugin.folder_name,
        container_path=plugin.container_path,
        remote_name=plugin.remote_name,
        mode=plugin.mode,
        oauth_authorize_url=plugin.oauth_authorize_url,
        oauth_token_url=plugin.oauth_token_url,
        drive_api_base_url=plugin.drive_api_base_url,
        credentials_json=plugin.credentials_json,
        public_base_url=plugin.public_base_url,
    )
    server.start_in_thread("127.0.0.1")
    _started_auth_servers.append(server)
    plugin.post_start(runtime)
    return runtime


def read_url(url: str) -> HttpResponse:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
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


def service_url(port: int, path: str) -> str:
    return f"http://127.0.0.1:{port}{path}"


def wait_for_status(port: int, state: str, *, timeout_seconds: float = 3.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_status: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        status = read_url(service_url(port, "/status")).json()
        last_status = status
        if status.get("state") == state:
            return status
        time.sleep(0.05)
    raise AssertionError(f"status did not become {state!r}: {last_status!r}")


@pytest.fixture
def fake_persistent_rclone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakePersistentRclone:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "rclone.log"
    mount_marker = tmp_path / "mounted"
    rclone_path = bin_dir / "rclone"
    rclone_path.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import pathlib
import sys

log_path = pathlib.Path(os.environ["FAKE_PERSISTENT_RCLONE_LOG"])
mount_marker = pathlib.Path(os.environ["FAKE_PERSISTENT_RCLONE_MOUNT_MARKER"])
args = sys.argv[1:]
with log_path.open("a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\\n")

config_env = os.environ.get("RCLONE_CONFIG")
if not config_env:
    print("RCLONE_CONFIG is required", file=sys.stderr)
    raise SystemExit(80)
config_path = pathlib.Path(config_env)
if config_path.parent.exists() and not config_path.parent.is_dir():
    print("RCLONE_CONFIG parent is not a directory", file=sys.stderr)
    raise SystemExit(81)

if not args:
    raise SystemExit(2)
if args[:2] == ["config", "delete"]:
    remote = args[2] if len(args) > 2 else ""
    if config_path.exists():
        lines = config_path.read_text(encoding="utf-8").splitlines()
        output = []
        skipping = False
        for line in lines:
            if line == f"[{remote}]":
                skipping = True
                continue
            if skipping and line.startswith("[") and line.endswith("]"):
                skipping = False
            if not skipping:
                output.append(line)
        config_path.write_text("\\n".join(output).strip() + "\\n", encoding="utf-8")
    mount_marker.unlink(missing_ok=True)
    raise SystemExit(0)
if args[:1] == ["config"]:
    if not config_path.exists():
        print("rclone config does not exist", file=sys.stderr)
        raise SystemExit(82)
    raise SystemExit(0)
if args[:1] == ["mount"]:
    if not config_path.exists():
        print("rclone config does not exist", file=sys.stderr)
        raise SystemExit(82)
    remote = args[1].rstrip(":") if len(args) > 1 else ""
    if f"[{remote}]" not in config_path.read_text(encoding="utf-8"):
        print("configured remote does not exist", file=sys.stderr)
        raise SystemExit(83)
    cache_dir = os.environ.get("RCLONE_CACHE_DIR")
    if not cache_dir:
        print("RCLONE_CACHE_DIR is required", file=sys.stderr)
        raise SystemExit(84)
    pathlib.Path(cache_dir).mkdir(parents=True, exist_ok=True)
    mount_marker.write_text("mounted", encoding="utf-8")
    raise SystemExit(0)
if args[:1] == ["lsf"]:
    if not config_path.exists():
        print("rclone config does not exist", file=sys.stderr)
        raise SystemExit(82)
    remote = args[1].rstrip(":") if len(args) > 1 else ""
    if f"[{remote}]" not in config_path.read_text(encoding="utf-8"):
        print("configured remote does not exist", file=sys.stderr)
        raise SystemExit(83)
    raise SystemExit(0)
if args[:1] == ["cat"]:
    if not config_path.exists():
        print("rclone config does not exist", file=sys.stderr)
        raise SystemExit(82)
    remote = args[1].split(":", 1)[0] if len(args) > 1 else ""
    if f"[{remote}]" not in config_path.read_text(encoding="utf-8"):
        print("configured remote does not exist", file=sys.stderr)
        raise SystemExit(83)
    print("ok", end="")
    raise SystemExit(0)
if args[:1] == ["deletefile"]:
    if not config_path.exists():
        print("rclone config does not exist", file=sys.stderr)
        raise SystemExit(82)
    raise SystemExit(0)
if args[:1] == ["cleanup"]:
    raise SystemExit(0)
print(f"unknown command {args[0]!r}", file=sys.stderr)
raise SystemExit(64)
""",
        encoding="utf-8",
    )
    rclone_path.chmod(0o755)
    unmount_tool_script = """#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import pathlib
import sys

log_path = pathlib.Path(os.environ["FAKE_PERSISTENT_RCLONE_LOG"])
mount_marker = pathlib.Path(os.environ["FAKE_PERSISTENT_RCLONE_MOUNT_MARKER"])
command = pathlib.Path(sys.argv[0]).name
args = [command, *sys.argv[1:]]
with log_path.open("a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\\n")
if command in {"fusermount3", "fusermount"}:
    if len(sys.argv) != 3 or sys.argv[1] != "-u":
        print(f"{command} expected '-u <target>'", file=sys.stderr)
        raise SystemExit(65)
else:
    if len(sys.argv) != 2:
        print("umount expected '<target>'", file=sys.stderr)
        raise SystemExit(65)
mount_marker.unlink(missing_ok=True)
raise SystemExit(0)
"""
    for tool_name in ("fusermount3", "fusermount", "umount"):
        tool_path = bin_dir / tool_name
        tool_path.write_text(unmount_tool_script, encoding="utf-8")
        tool_path.chmod(0o755)
    mountpoint_path = bin_dir / "mountpoint"
    mountpoint_path.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations

import os
import pathlib

mount_marker = pathlib.Path(os.environ["FAKE_PERSISTENT_RCLONE_MOUNT_MARKER"])
raise SystemExit(0 if mount_marker.exists() else 1)
""",
        encoding="utf-8",
    )
    mountpoint_path.chmod(0o755)
    monkeypatch.setenv("FAKE_PERSISTENT_RCLONE_LOG", str(log_path))
    monkeypatch.setenv("FAKE_PERSISTENT_RCLONE_MOUNT_MARKER", str(mount_marker))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return FakePersistentRclone(log_path=log_path, mount_marker=mount_marker)
