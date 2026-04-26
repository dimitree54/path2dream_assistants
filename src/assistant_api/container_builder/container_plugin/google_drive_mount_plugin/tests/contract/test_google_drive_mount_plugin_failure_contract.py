from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from assistant_api.container_builder import ContainerBuilderService
from google_drive_mount_contract_helpers import (
    assert_error_status,
    auth_port,
    complete_oauth_flow,
    service_class,
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
    }
