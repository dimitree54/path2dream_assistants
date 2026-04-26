from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from assistant_api.models import ContainerRuntimeContext


_started_auth_servers: list[object] = []


@dataclass(slots=True)
class FakeRclone:
    log_path: Path
    config_marker: Path
    mount_marker: Path

    def commands(self) -> list[list[str]]:
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text().splitlines()]


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
        command_text = " ".join(command)
        if "mountpoint" in command_text:
            if os.environ.get("FAKE_MOUNTPOINT_FAIL") == "1":
                return ExecRunResult(1, "not a mountpoint")
            return ExecRunResult(0 if self.mount_marker.exists() else 1)
        if command and command[0] == "rclone":
            result = subprocess.run(command, check=False, capture_output=True, text=True)
            return ExecRunResult(result.returncode, result.stdout + result.stderr)
        return ExecRunResult(0)


def start_plugin(
    plugin: object,
    state: dict[str, object],
    fake_rclone: FakeRclone,
) -> ContainerRuntimeContext:
    from assistant_api.container_builder.container_plugin.google_drive_mount_plugin._auth_server import (
        GoogleDriveMountAuthServer,
    )

    runtime = ContainerRuntimeContext(
        docker_client=object(),
        container=FakeContainer(fake_rclone.mount_marker),
        state=state,
    )
    plugin.post_start(runtime)
    server = GoogleDriveMountAuthServer(
        auth_port=plugin.auth_container_port,
        host_port=plugin.host_port,
        drive_folder_name=plugin.folder_name,
        container_path=plugin.container_path,
        remote_name=plugin.remote_name,
        oauth_authorize_url=plugin.oauth_authorize_url,
        oauth_token_url=plugin.oauth_token_url,
        drive_api_base_url=plugin.drive_api_base_url,
        credentials_json=plugin.credentials_json,
    )
    server.start_in_thread("127.0.0.1")
    _started_auth_servers.append(server)
    return runtime


@pytest.fixture
def fake_rclone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeRclone:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "rclone.log"
    config_marker = tmp_path / "configured"
    mount_marker = tmp_path / "mounted"
    rclone_path = bin_dir / "rclone"
    rclone_path.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import pathlib
import sys
import time

log_path = pathlib.Path(os.environ["FAKE_RCLONE_LOG"])
config_marker = pathlib.Path(os.environ["FAKE_RCLONE_CONFIGURED"])
mount_marker = pathlib.Path(os.environ["FAKE_RCLONE_MOUNT_MARKER"])
args = sys.argv[1:]
with log_path.open("a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\\n")
if not args:
    raise SystemExit(2)
if args[:2] == ["config", "delete"]:
    config_marker.unlink(missing_ok=True)
    raise SystemExit(0)
if args[0] == "config":
    config_marker.write_text(json.dumps(args), encoding="utf-8")
    raise SystemExit(0)
if args[0] == "mount":
    if os.environ.get("FAKE_RCLONE_FAIL_MOUNT") == "1":
        raise SystemExit(51)
    if not config_marker.exists():
        raise SystemExit(52)
    mount_marker.write_text("mounted", encoding="utf-8")
    time.sleep(float(os.environ.get("FAKE_RCLONE_MOUNT_SECONDS", "0.05")))
    raise SystemExit(0)
if args[0] in {"unmount", "cleanup"}:
    mount_marker.unlink(missing_ok=True)
    raise SystemExit(0)
raise SystemExit(0)
""",
        encoding="utf-8",
    )
    rclone_path.chmod(0o755)
    mountpoint_path = bin_dir / "mountpoint"
    mountpoint_path.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations

import os
import pathlib

mount_marker = pathlib.Path(os.environ["FAKE_RCLONE_MOUNT_MARKER"])
if os.environ.get("FAKE_MOUNTPOINT_FAIL") == "1":
    raise SystemExit(1)
raise SystemExit(0 if mount_marker.exists() else 1)
""",
        encoding="utf-8",
    )
    mountpoint_path.chmod(0o755)
    monkeypatch.setenv("FAKE_RCLONE_LOG", str(log_path))
    monkeypatch.setenv("FAKE_RCLONE_CONFIGURED", str(config_marker))
    monkeypatch.setenv("FAKE_RCLONE_MOUNT_MARKER", str(mount_marker))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return FakeRclone(
        log_path=log_path,
        config_marker=config_marker,
        mount_marker=mount_marker,
    )
