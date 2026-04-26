from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

import pytest

from assistant_api.container_builder import ContainerBuilderService
from google_drive_persistence_contract_helpers import (
    FakePersistentRclone,
    fake_persistent_rclone,
    mount_service_class,
    persistence_service_class,
    read_url,
    service_url,
    start_mount_plugin,
    wait_for_status,
)


def write_rclone_config(config_file: Path, remote_name: str = "gdrive") -> None:
    config_file.parent.mkdir(parents=True, exist_ok=True)
    token = json.dumps({"access_token": "persisted-access-token", "refresh_token": "refresh-token"})
    config_file.write_text(
        "\n".join(
            [
                f"[{remote_name}]",
                "type = drive",
                "scope = drive.file",
                "root_folder_id = persisted-folder-id",
                f"token = {token}",
                "",
                "[other]",
                "type = drive",
                "scope = drive.file",
                "root_folder_id = unrelated-folder-id",
                "",
            ]
        ),
        encoding="utf-8",
    )


@pytest.fixture
def google_mount_env(monkeypatch: pytest.MonkeyPatch) -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    monkeypatch.setenv(
        "GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON",
        json.dumps({"web": {"client_id": "client-id", "client_secret": "client-secret"}}),
    )
    return port


def test_mount_plugin_restores_valid_persisted_auth_without_browser_login(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    google_mount_env: int,
    fake_persistent_rclone: FakePersistentRclone,
) -> None:
    config_dir = tmp_path / "persisted-config"
    cache_dir = tmp_path / "persisted-cache"
    config_file = config_dir / "rclone.conf"
    write_rclone_config(config_file)
    persistence_plugin = persistence_service_class()(
        config_dir=PurePosixPath(config_dir),
        cache_dir=PurePosixPath(cache_dir),
    )
    mount_plugin = mount_service_class()(
        host_port=google_mount_env,
        drive_folder_name="Persisted Drive Folder",
        container_path=PurePosixPath(tmp_path / "project"),
        drive_api_base_url="http://127.0.0.1:1",
    )
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[persistence_plugin, mount_plugin]
    )._prepare_specs()
    assert container_spec.startup_tasks, (
        "Persisted Google Drive restore must run as blocking startup work before auth serving"
    )
    for name, value in container_spec.env.items():
        monkeypatch.setenv(name, value)

    start_mount_plugin(mount_plugin, container_spec, container_spec.state, fake_persistent_rclone)

    status = wait_for_status(google_mount_env, "mounted")
    assert status["authValid"] is True
    assert status["mounted"] is True
    assert fake_persistent_rclone.mount_marker.exists()
    assert cache_dir.exists()
    commands = fake_persistent_rclone.commands()
    assert any(command[:1] == ["mount"] and command[1] == "gdrive:" for command in commands)
    assert all(command[:1] != ["config"] or command[:3] != ["config", "create", "gdrive"] for command in commands)


def test_mount_plugin_keeps_login_logout_and_status_routes_when_persistence_is_composed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    google_mount_env: int,
    fake_persistent_rclone: FakePersistentRclone,
) -> None:
    persistence_plugin = persistence_service_class()(
        config_dir=PurePosixPath(tmp_path / "empty-config"),
        cache_dir=PurePosixPath(tmp_path / "cache"),
    )
    mount_plugin = mount_service_class()(
        host_port=google_mount_env,
        drive_folder_name="Persisted Drive Folder",
        container_path=PurePosixPath(tmp_path / "project"),
        drive_api_base_url="http://127.0.0.1:1",
    )
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[persistence_plugin, mount_plugin]
    )._prepare_specs()
    for name, value in container_spec.env.items():
        monkeypatch.setenv(name, value)

    start_mount_plugin(mount_plugin, container_spec, container_spec.state, fake_persistent_rclone)

    status_response = read_url(service_url(google_mount_env, "/status"))
    login_response = read_url(service_url(google_mount_env, "/login"))
    logout_response = read_url(service_url(google_mount_env, "/logout"))

    assert status_response.status == 200
    assert status_response.json()["authValid"] is False
    assert login_response.status == 200
    assert "text/html" in login_response.headers.get("Content-Type", "")
    assert logout_response.status == 200


def test_logout_clears_only_configured_remote_from_persisted_rclone_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    google_mount_env: int,
    fake_persistent_rclone: FakePersistentRclone,
) -> None:
    config_dir = tmp_path / "persisted-config"
    cache_dir = tmp_path / "persisted-cache"
    config_file = config_dir / "rclone.conf"
    write_rclone_config(config_file)
    persistence_plugin = persistence_service_class()(
        config_dir=PurePosixPath(config_dir),
        cache_dir=PurePosixPath(cache_dir),
    )
    mount_plugin = mount_service_class()(
        host_port=google_mount_env,
        drive_folder_name="Persisted Drive Folder",
        container_path=PurePosixPath(tmp_path / "project"),
        remote_name="gdrive",
        drive_api_base_url="http://127.0.0.1:1",
    )
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[persistence_plugin, mount_plugin]
    )._prepare_specs()
    for name, value in container_spec.env.items():
        monkeypatch.setenv(name, value)
    start_mount_plugin(mount_plugin, container_spec, container_spec.state, fake_persistent_rclone)
    assert wait_for_status(google_mount_env, "mounted")["authValid"] is True

    logout = read_url(service_url(google_mount_env, "/logout"))

    assert logout.status == 200
    status = read_url(service_url(google_mount_env, "/status")).json()
    assert status["authValid"] is False
    assert status["mounted"] is False
    persisted_config = config_file.read_text(encoding="utf-8")
    assert "[gdrive]" not in persisted_config
    assert "persisted-access-token" not in persisted_config
    assert "refresh-token" not in persisted_config
    assert "[other]" in persisted_config
    assert "unrelated-folder-id" in persisted_config


def test_mount_plugin_fails_fast_when_persisted_rclone_config_path_cannot_be_used(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    google_mount_env: int,
    fake_persistent_rclone: FakePersistentRclone,
) -> None:
    blocked_config_dir = tmp_path / "blocked-config-dir"
    blocked_config_dir.write_text("this path is a file, not a directory", encoding="utf-8")
    persistence_plugin = persistence_service_class()(
        config_dir=PurePosixPath(blocked_config_dir),
        cache_dir=PurePosixPath(tmp_path / "cache"),
    )
    mount_plugin = mount_service_class()(
        host_port=google_mount_env,
        drive_folder_name="Persisted Drive Folder",
        container_path=PurePosixPath(tmp_path / "project"),
        drive_api_base_url="http://127.0.0.1:1",
    )
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[persistence_plugin, mount_plugin]
    )._prepare_specs()
    for name, value in container_spec.env.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match="RCLONE_CONFIG|rclone config|persisted"):
        start_mount_plugin(mount_plugin, container_spec, container_spec.state, fake_persistent_rclone)
