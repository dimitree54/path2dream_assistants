from __future__ import annotations

from pathlib import PurePosixPath

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin import MOUNT_METADATA_STATE_KEY
from google_drive_mount_contract_helpers import (
    FORBIDDEN_SCOPES,
    GOOGLE_DRIVE_FILE_SCOPE,
    assert_rclone_config_precedes_mount,
    complete_oauth_flow,
    extract_login_href,
    read_url,
    service_class,
    service_url,
    status_json,
    unused_port,
)
from google_drive_mount_oauth_stub import OAuthDriveStub, google_env, oauth_drive_stub
from google_drive_mount_rclone_stub import FakeRclone, fake_rclone, start_plugin


def test_login_page_uses_custom_authorize_url_and_only_drive_file_scope(
    google_env: str,
    oauth_drive_stub: OAuthDriveStub,
    fake_rclone: FakeRclone,
) -> None:
    host_port = unused_port()
    plugin = service_class()(
        host_port=host_port,
        oauth_authorize_url=oauth_drive_stub.authorize_url,
        oauth_token_url=oauth_drive_stub.token_url,
        drive_api_base_url=oauth_drive_stub.drive_api_base_url,
    )
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()
    start_plugin(plugin, container_spec.state, fake_rclone)

    status = status_json(host_port)
    assert status["state"] == "unauthenticated"
    assert status["authValid"] is False
    assert status["mounted"] is False

    login = read_url(service_url(host_port, "/login"))
    assert login.status == 200
    assert "text/html" in login.headers.get("Content-Type", "")
    authorize_url = extract_login_href(login.text)
    authorize_response = read_url(authorize_url, allow_redirects=False)

    assert authorize_response.status in {302, 303}
    authorize_query = oauth_drive_stub.state.authorize_queries[-1]
    assert authorize_query["client_id"] == ["client-id"]
    assert authorize_query["scope"] == [GOOGLE_DRIVE_FILE_SCOPE]
    requested_scopes = set(authorize_query["scope"][0].split())
    assert GOOGLE_DRIVE_FILE_SCOPE in requested_scopes
    assert requested_scopes.isdisjoint(FORBIDDEN_SCOPES)
    assert google_env in container_spec.state[MOUNT_METADATA_STATE_KEY].host_basename


def test_oauth_callback_creates_folder_configures_rclone_and_reports_mounted(
    oauth_drive_stub: OAuthDriveStub,
    fake_rclone: FakeRclone,
) -> None:
    host_port = unused_port()
    plugin = service_class()(
        host_port=host_port,
        oauth_authorize_url=oauth_drive_stub.authorize_url,
        oauth_token_url=oauth_drive_stub.token_url,
        drive_api_base_url=oauth_drive_stub.drive_api_base_url,
    )
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()
    start_plugin(plugin, container_spec.state, fake_rclone)

    callback = complete_oauth_flow(host_port)

    assert callback.status in {200, 302, 303}
    status = status_json(host_port)
    assert status["state"] == "mounted"
    assert status["authValid"] is True
    assert status["mounted"] is True
    assert oauth_drive_stub.state.token_requests[0]["code"] == ["oauth-code"]
    assert oauth_drive_stub.state.created_folders == [
        {
            "name": oauth_drive_stub.state.expected_folder_name,
            "mimeType": "application/vnd.google-apps.folder",
        }
    ]
    mount = container_spec.state[MOUNT_METADATA_STATE_KEY]
    assert mount.remote_folder_id == oauth_drive_stub.state.created_folder_id
    assert_rclone_config_precedes_mount(
        fake_rclone,
        remote_name="gdrive",
        folder_id=oauth_drive_stub.state.created_folder_id,
        container_path=PurePosixPath("/workspace/project"),
    )


def test_oauth_callback_reuses_existing_folder_without_duplicate_creation(
    oauth_drive_stub: OAuthDriveStub,
    fake_rclone: FakeRclone,
) -> None:
    oauth_drive_stub.state.existing_folder_id = "existing-folder-id"
    host_port = unused_port()
    plugin = service_class()(
        host_port=host_port,
        oauth_authorize_url=oauth_drive_stub.authorize_url,
        oauth_token_url=oauth_drive_stub.token_url,
        drive_api_base_url=oauth_drive_stub.drive_api_base_url,
    )
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()
    start_plugin(plugin, container_spec.state, fake_rclone)

    callback = complete_oauth_flow(host_port)

    assert callback.status in {200, 302, 303}
    assert status_json(host_port)["mounted"] is True
    assert oauth_drive_stub.state.created_folders == []
    mount = container_spec.state[MOUNT_METADATA_STATE_KEY]
    assert mount.remote_folder_id == "existing-folder-id"
    assert_rclone_config_precedes_mount(
        fake_rclone,
        remote_name="gdrive",
        folder_id="existing-folder-id",
        container_path=PurePosixPath("/workspace/project"),
    )


def test_logout_unmounts_and_clears_auth_config(
    oauth_drive_stub: OAuthDriveStub,
    fake_rclone: FakeRclone,
) -> None:
    host_port = unused_port()
    plugin = service_class()(
        host_port=host_port,
        oauth_authorize_url=oauth_drive_stub.authorize_url,
        oauth_token_url=oauth_drive_stub.token_url,
        drive_api_base_url=oauth_drive_stub.drive_api_base_url,
    )
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()
    start_plugin(plugin, container_spec.state, fake_rclone)
    callback = complete_oauth_flow(host_port)
    assert callback.status in {200, 302, 303}
    assert status_json(host_port)["mounted"] is True

    logout = read_url(service_url(host_port, "/logout"))

    assert logout.status == 200
    status = status_json(host_port)
    assert status["state"] == "unauthenticated"
    assert status["authValid"] is False
    assert status["mounted"] is False
    assert fake_rclone.mount_marker.exists() is False
    assert fake_rclone.config_marker.exists() is False
    assert any(command[:1] in (["unmount"], ["cleanup"]) for command in fake_rclone.commands())
