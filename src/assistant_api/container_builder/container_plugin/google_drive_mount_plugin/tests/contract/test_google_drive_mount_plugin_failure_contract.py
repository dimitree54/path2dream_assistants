from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest

from assistant_api.container_builder import ContainerBuilderService
from google_drive_mount_contract_helpers import (
    assert_error_status,
    auth_port,
    complete_oauth_flow,
    read_url,
    service_class,
    service_url,
    status_json,
)
from google_drive_mount_oauth_stub import OAuthDriveStub, google_env, oauth_drive_stub
from google_drive_mount_rclone_stub import FakeRclone, fake_rclone, start_plugin


@pytest.mark.parametrize(
    "failure_name",
    [
        "oauth_denied",
        "token_failure",
        "drive_failure",
        "rclone_failure",
        "mountpoint_failure",
        "remote_probe_failure",
        "remote_write_probe_failure",
        "non_empty_mount_target",
    ],
)
def test_flow_failures_report_error_and_never_fallback_to_local_mount(
    oauth_drive_stub: OAuthDriveStub,
    fake_rclone: FakeRclone,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_name: str,
) -> None:
    if failure_name in {"oauth_denied", "token_failure", "drive_failure"}:
        setattr(oauth_drive_stub.state, failure_name, True)
    if failure_name == "rclone_failure":
        monkeypatch.setenv("FAKE_RCLONE_FAIL_MOUNT", "1")
    if failure_name == "mountpoint_failure":
        monkeypatch.setenv("FAKE_MOUNTPOINT_FAIL", "1")
    if failure_name == "remote_probe_failure":
        monkeypatch.setenv("FAKE_RCLONE_FAIL_LSF", "1")
    if failure_name == "remote_write_probe_failure":
        monkeypatch.setenv("FAKE_RCLONE_FAIL_CAT", "1")
        monkeypatch.setenv("GOOGLE_DRIVE_REMOTE_WRITE_PROBE_TIMEOUT_SECONDS", "0.1")
    container_path = PurePosixPath(tmp_path / "project")
    if failure_name == "non_empty_mount_target":
        target = Path(container_path)
        target.mkdir(parents=True)
        (target / "preexisting-file.txt").write_text("not empty", encoding="utf-8")
    host_port = auth_port()
    plugin = service_class()(
        host_port=host_port,
        drive_folder_name=oauth_drive_stub.state.expected_folder_name,
        container_path=container_path,
        oauth_authorize_url=oauth_drive_stub.authorize_url,
        oauth_token_url=oauth_drive_stub.token_url,
        drive_api_base_url=oauth_drive_stub.drive_api_base_url,
    )
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()
    start_plugin(plugin, container_spec.state, fake_rclone)

    complete_oauth_flow(host_port)

    assert_error_status(host_port)
    assert container_spec.volumes == {}
    assert all(command[:1] != ["mount"] for command in fake_rclone.commands()) or failure_name in {
        "rclone_failure",
        "mountpoint_failure",
        "remote_probe_failure",
        "remote_write_probe_failure",
    }


def test_restore_persisted_mount_exits_nonzero_when_remote_probe_fails(
    google_env: str,
    fake_rclone: FakeRclone,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from assistant_api.container_builder.container_plugin.google_drive_mount_plugin import _auth_server

    config_file = tmp_path / "rclone.conf"
    config_file.write_text("[gdrive]\ntype = drive\n", encoding="utf-8")
    fake_rclone.config_marker.write_text("configured", encoding="utf-8")
    monkeypatch.setenv("RCLONE_CONFIG", str(config_file))
    monkeypatch.setenv("RCLONE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("GOOGLE_DRIVE_AUTH_PORT", str(auth_port()))
    monkeypatch.setenv("GOOGLE_DRIVE_AUTH_HOST_PORT", str(auth_port()))
    monkeypatch.setenv("GOOGLE_DRIVE_MOUNT_FOLDER_NAME", google_env)
    monkeypatch.setenv("GOOGLE_DRIVE_MOUNT_CONTAINER_PATH", str(tmp_path / "project"))
    monkeypatch.setenv("GOOGLE_DRIVE_REMOTE_NAME", "gdrive")
    monkeypatch.setenv("GOOGLE_DRIVE_MOUNT_MODE", "rw")
    monkeypatch.setenv("GOOGLE_DRIVE_OAUTH_AUTHORIZE_URL", "http://127.0.0.1:1/oauth/authorize")
    monkeypatch.setenv("GOOGLE_DRIVE_OAUTH_TOKEN_URL", "http://127.0.0.1:1/oauth/token")
    monkeypatch.setenv("GOOGLE_DRIVE_API_BASE_URL", "http://127.0.0.1:1/drive/v3")
    monkeypatch.setenv("FAKE_RCLONE_FAIL_LSF", "1")

    result = subprocess.run(
        [sys.executable, str(Path(_auth_server.__file__)), "--restore-persisted-mount"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Google Drive remote read verification failed" in result.stderr
    assert not fake_rclone.mount_marker.exists()


def test_restore_persisted_mount_normalizes_legacy_token_expiry(
    google_env: str,
    fake_rclone: FakeRclone,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from assistant_api.container_builder.container_plugin.google_drive_mount_plugin import _auth_server

    config_file = tmp_path / "rclone.conf"
    legacy_token = {
        "access_token": "expired-access-token",
        "refresh_token": "refresh-token",
        "expires_in": 3599,
        "token_type": "Bearer",
    }
    config_file.write_text(
        "[gdrive]\n"
        "type = drive\n"
        f"token = {json.dumps(legacy_token)}\n",
        encoding="utf-8",
    )
    fake_rclone.config_marker.write_text("configured", encoding="utf-8")
    monkeypatch.setenv("RCLONE_CONFIG", str(config_file))
    monkeypatch.setenv("RCLONE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("GOOGLE_DRIVE_AUTH_PORT", str(auth_port()))
    monkeypatch.setenv("GOOGLE_DRIVE_AUTH_HOST_PORT", str(auth_port()))
    monkeypatch.setenv("GOOGLE_DRIVE_MOUNT_FOLDER_NAME", google_env)
    monkeypatch.setenv("GOOGLE_DRIVE_MOUNT_CONTAINER_PATH", str(tmp_path / "project"))
    monkeypatch.setenv("GOOGLE_DRIVE_REMOTE_NAME", "gdrive")
    monkeypatch.setenv("GOOGLE_DRIVE_MOUNT_MODE", "rw")
    monkeypatch.setenv("GOOGLE_DRIVE_OAUTH_AUTHORIZE_URL", "http://127.0.0.1:1/oauth/authorize")
    monkeypatch.setenv("GOOGLE_DRIVE_OAUTH_TOKEN_URL", "http://127.0.0.1:1/oauth/token")
    monkeypatch.setenv("GOOGLE_DRIVE_API_BASE_URL", "http://127.0.0.1:1/drive/v3")

    result = subprocess.run(
        [sys.executable, str(Path(_auth_server.__file__)), "--restore-persisted-mount"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    token_line = next(
        line for line in config_file.read_text(encoding="utf-8").splitlines()
        if line.startswith("token = ")
    )
    normalized_token = json.loads(token_line.split("=", 1)[1])
    assert normalized_token["expiry"] == "1970-01-01T00:00:00Z"
    assert "expires_in" not in normalized_token
    assert normalized_token["refresh_token"] == "refresh-token"


def test_restore_persisted_mount_forces_refresh_after_invalid_access_token(
    google_env: str,
    fake_rclone: FakeRclone,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from assistant_api.container_builder.container_plugin.google_drive_mount_plugin import _auth_server

    config_file = tmp_path / "rclone.conf"
    stale_token = {
        "access_token": "invalid-access-token",
        "refresh_token": "refresh-token",
        "expiry": "2099-01-01T00:00:00Z",
        "token_type": "Bearer",
    }
    config_file.write_text(
        "[gdrive]\n"
        "type = drive\n"
        f"token = {json.dumps(stale_token)}\n",
        encoding="utf-8",
    )
    fake_rclone.config_marker.write_text("configured", encoding="utf-8")
    monkeypatch.setenv("RCLONE_CONFIG", str(config_file))
    monkeypatch.setenv("RCLONE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("GOOGLE_DRIVE_AUTH_PORT", str(auth_port()))
    monkeypatch.setenv("GOOGLE_DRIVE_AUTH_HOST_PORT", str(auth_port()))
    monkeypatch.setenv("GOOGLE_DRIVE_MOUNT_FOLDER_NAME", google_env)
    monkeypatch.setenv("GOOGLE_DRIVE_MOUNT_CONTAINER_PATH", str(tmp_path / "project"))
    monkeypatch.setenv("GOOGLE_DRIVE_REMOTE_NAME", "gdrive")
    monkeypatch.setenv("GOOGLE_DRIVE_MOUNT_MODE", "rw")
    monkeypatch.setenv("GOOGLE_DRIVE_OAUTH_AUTHORIZE_URL", "http://127.0.0.1:1/oauth/authorize")
    monkeypatch.setenv("GOOGLE_DRIVE_OAUTH_TOKEN_URL", "http://127.0.0.1:1/oauth/token")
    monkeypatch.setenv("GOOGLE_DRIVE_API_BASE_URL", "http://127.0.0.1:1/drive/v3")
    monkeypatch.setenv("FAKE_RCLONE_REQUIRE_EXPIRED_TOKEN", "1")

    result = subprocess.run(
        [sys.executable, str(Path(_auth_server.__file__)), "--restore-persisted-mount"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    token_line = next(
        line for line in config_file.read_text(encoding="utf-8").splitlines()
        if line.startswith("token = ")
    )
    normalized_token = json.loads(token_line.split("=", 1)[1])
    assert normalized_token["expiry"] == "1970-01-01T00:00:00Z"


def test_existing_mount_state_reports_error_when_remote_probe_fails(
    oauth_drive_stub: OAuthDriveStub,
    fake_rclone: FakeRclone,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "rclone.conf"
    config_file.write_text("[gdrive]\ntype = drive\n", encoding="utf-8")
    fake_rclone.config_marker.write_text("configured", encoding="utf-8")
    fake_rclone.mount_marker.write_text("mounted", encoding="utf-8")
    monkeypatch.setenv("RCLONE_CONFIG", str(config_file))
    monkeypatch.setenv("FAKE_RCLONE_FAIL_LSF", "1")
    host_port = auth_port()
    plugin = service_class()(
        host_port=host_port,
        drive_folder_name=oauth_drive_stub.state.expected_folder_name,
        container_path=PurePosixPath(tmp_path / "project"),
        oauth_authorize_url=oauth_drive_stub.authorize_url,
        oauth_token_url=oauth_drive_stub.token_url,
        drive_api_base_url=oauth_drive_stub.drive_api_base_url,
    )
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()

    with pytest.raises(RuntimeError, match="Google Drive mount health check failed"):
        start_plugin(plugin, container_spec.state, fake_rclone)

    assert_error_status(host_port)
    login = read_url(service_url(host_port, "/login"))
    assert "Google Drive is mounted successfully" not in login.text


def test_status_detects_mount_degradation_after_successful_mount(
    oauth_drive_stub: OAuthDriveStub,
    fake_rclone: FakeRclone,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host_port = auth_port()
    plugin = service_class()(
        host_port=host_port,
        drive_folder_name=oauth_drive_stub.state.expected_folder_name,
        container_path=PurePosixPath(tmp_path / "project"),
        oauth_authorize_url=oauth_drive_stub.authorize_url,
        oauth_token_url=oauth_drive_stub.token_url,
        drive_api_base_url=oauth_drive_stub.drive_api_base_url,
    )
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()
    start_plugin(plugin, container_spec.state, fake_rclone)

    complete_oauth_flow(host_port)

    status = read_url(service_url(host_port, "/status"))
    assert status.status == 200
    payload = status.json()
    assert payload["state"] == "mounted"
    assert payload["mounted"] is True
    assert payload["authValid"] is True

    monkeypatch.setenv("FAKE_RCLONE_FAIL_LSF", "1")

    status = read_url(service_url(host_port, "/status"))
    assert status.status == 200
    payload = status.json()
    assert payload["state"] == "error"
    assert payload["mounted"] is False
    assert payload["authValid"] is False
    assert payload["message"]


def test_fake_rclone_rejects_unknown_commands(fake_rclone: FakeRclone) -> None:
    result = subprocess.run(
        ["rclone", "unmount", "/workspace"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "unknown command" in result.stderr
    assert fake_rclone.commands() == [["unmount", "/workspace"]]


def test_reauth_replaces_stale_active_mountpoint_before_remount(
    oauth_drive_stub: OAuthDriveStub,
    fake_rclone: FakeRclone,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host_port = auth_port()
    container_path = tmp_path / "project"
    plugin = service_class()(
        host_port=host_port,
        drive_folder_name=oauth_drive_stub.state.expected_folder_name,
        container_path=PurePosixPath(container_path),
        oauth_authorize_url=oauth_drive_stub.authorize_url,
        oauth_token_url=oauth_drive_stub.token_url,
        drive_api_base_url=oauth_drive_stub.drive_api_base_url,
    )
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()
    start_plugin(plugin, container_spec.state, fake_rclone)

    complete_oauth_flow(host_port)
    assert status_json(host_port)["state"] == "mounted"
    visible_drive_file = container_path / "remote-visible-note.txt"
    visible_drive_file.write_text("visible through active FUSE mount", encoding="utf-8")

    monkeypatch.setenv("FAKE_RCLONE_FAIL_LSF", "1")
    degraded_status = status_json(host_port)
    assert degraded_status["state"] == "error"
    assert degraded_status["mounted"] is False
    assert fake_rclone.mount_marker.exists()
    assert visible_drive_file.exists()

    monkeypatch.delenv("FAKE_RCLONE_FAIL_LSF")
    monkeypatch.setenv("FAKE_CLEAR_MOUNT_TARGET_ON_UNMOUNT", "1")
    callback = complete_oauth_flow(host_port)

    assert callback.status in {200, 302, 303}
    recovered_status = status_json(host_port)
    assert recovered_status["state"] == "mounted"
    assert recovered_status["mounted"] is True
    assert recovered_status["authValid"] is True
    assert not visible_drive_file.exists()
    commands = fake_rclone.commands()
    mount_indexes = [index for index, command in enumerate(commands) if command[:1] == ["mount"]]
    unmount_indexes = [
        index for index, command in enumerate(commands)
        if command[:2] == ["fusermount3", "-u"]
    ]
    assert len(mount_indexes) == 2
    assert unmount_indexes
    assert mount_indexes[0] < unmount_indexes[-1] < mount_indexes[1]
    assert all(command[:1] != ["unmount"] for command in commands)
    assert all("--allow-non-empty" not in command for command in commands)


def test_reauth_reports_error_when_stale_mountpoint_cannot_unmount(
    oauth_drive_stub: OAuthDriveStub,
    fake_rclone: FakeRclone,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host_port = auth_port()
    container_path = tmp_path / "project"
    plugin = service_class()(
        host_port=host_port,
        drive_folder_name=oauth_drive_stub.state.expected_folder_name,
        container_path=PurePosixPath(container_path),
        oauth_authorize_url=oauth_drive_stub.authorize_url,
        oauth_token_url=oauth_drive_stub.token_url,
        drive_api_base_url=oauth_drive_stub.drive_api_base_url,
    )
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()
    start_plugin(plugin, container_spec.state, fake_rclone)

    complete_oauth_flow(host_port)
    assert status_json(host_port)["state"] == "mounted"
    (container_path / "remote-visible-note.txt").write_text(
        "visible through active FUSE mount",
        encoding="utf-8",
    )

    monkeypatch.setenv("FAKE_RCLONE_FAIL_LSF", "1")
    assert status_json(host_port)["state"] == "error"

    monkeypatch.delenv("FAKE_RCLONE_FAIL_LSF")
    monkeypatch.setenv("FAKE_FUSERMOUNT3_FAIL_UNMOUNT", "1")
    callback = complete_oauth_flow(host_port)

    assert callback.status == 500
    assert "unmount failed" in callback.text
    failed_status = status_json(host_port)
    assert failed_status["state"] == "error"
    assert failed_status["mounted"] is False
    assert "unmount failed" in failed_status["message"]
    commands = fake_rclone.commands()
    assert len([command for command in commands if command[:1] == ["mount"]]) == 1
    assert any(command[:2] == ["fusermount3", "-u"] for command in commands)
    assert all(command[:1] != ["unmount"] for command in commands)
    assert all("--allow-non-empty" not in command for command in commands)
