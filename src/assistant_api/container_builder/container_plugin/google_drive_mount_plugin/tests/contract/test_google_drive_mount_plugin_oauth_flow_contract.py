from __future__ import annotations

import json
import urllib.parse
from importlib import resources
from pathlib import Path, PurePosixPath

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
    auth_port,
)
from google_drive_mount_oauth_stub import OAuthDriveStub, google_env, oauth_drive_stub
from google_drive_mount_rclone_stub import FakeRclone, fake_rclone, start_plugin


SHARED_STYLE_ASSET_NAME = "petprojectcofounder_login_page.css"


def test_login_page_uses_custom_authorize_url_and_only_drive_file_scope(
    google_env: str,
    oauth_drive_stub: OAuthDriveStub,
    fake_rclone: FakeRclone,
) -> None:
    host_port = auth_port()
    plugin = service_class()(
        host_port=host_port,
        drive_folder_name=google_env,
        container_path=PurePosixPath("/workspace/project"),
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
    assert "<title>Connect Google Drive | Pet Project Cofounder</title>" in login.text
    assert "Pet Project Cofounder" in login.text
    assert "Connect your Drive" in login.text
    assert "Secure Google Drive Mount" in login.text
    assert "Authorize Google Drive" in login.text
    assert "data:image/png;base64," in login.text
    assert _style_block(login.text) == _shared_page_style()
    assert "<body><a" not in login.text
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


def test_login_page_uses_repository_lfs_brand_asset() -> None:
    asset = resources.files(
        "assistant_api.container_builder.container_plugin.google_drive_mount_plugin"
    ).joinpath("assets", "petprojectcofounder_logo_small.PNG")

    assert asset.is_file()
    assert asset.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_login_page_can_render_from_standalone_container_module(
    monkeypatch,
) -> None:
    from assistant_api.container_builder.container_plugin.google_drive_mount_plugin import _login_page

    monkeypatch.setattr(_login_page, "__package__", "")

    html = _login_page.render_login_page("https://accounts.example/auth", "notes")
    success_html = _login_page.render_mount_success_page("notes")

    assert "data:image/png;base64," in html
    assert _style_block(html) == _shared_page_style()
    assert "Authorize Google Drive" in html
    assert "data:image/png;base64," in success_html
    assert _style_block(success_html) == _shared_page_style()
    assert "Proceed to using the Assistant" in success_html
    assert 'href="/logout"' in success_html


def test_oauth_callback_creates_folder_configures_rclone_and_reports_mounted(
    oauth_drive_stub: OAuthDriveStub,
    fake_rclone: FakeRclone,
    tmp_path: Path,
) -> None:
    host_port = auth_port()
    container_path = PurePosixPath(str(tmp_path / "project"))
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

    callback = complete_oauth_flow(host_port)

    assert callback.status in {200, 302, 303}
    assert "text/html" in callback.headers.get("Content-Type", "")
    assert "<title>Google Drive Connected | Pet Project Cofounder</title>" in callback.text
    assert "Drive connected" in callback.text
    assert "Google Drive is mounted successfully" in callback.text
    assert "Proceed to using the Assistant" in callback.text
    assert "Log out" in callback.text
    assert 'href="/logout"' in callback.text
    assert _style_block(callback.text) == _shared_page_style()
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
    assert_rclone_config_precedes_mount(
        fake_rclone,
        remote_name="gdrive",
        folder_id=oauth_drive_stub.state.created_folder_id,
        container_path=container_path,
    )
    config_command = next(
        command
        for command in fake_rclone.commands()
        if command[:3] == ["config", "create", "gdrive"]
    )
    rclone_token = json.loads(config_command[config_command.index("token") + 1])
    assert "expiry" in rclone_token
    assert "expires_in" not in rclone_token
    assert rclone_token["refresh_token"] == "refresh-token"
    authorize_request_count = len(oauth_drive_stub.state.authorize_queries)
    logged_in_page = read_url(service_url(host_port, "/login"))
    assert logged_in_page.status == 200
    assert "Google Drive is mounted successfully" in logged_in_page.text
    assert 'href="/logout"' in logged_in_page.text
    assert len(oauth_drive_stub.state.authorize_queries) == authorize_request_count


def test_oauth_callback_reuses_existing_folder_without_duplicate_creation(
    oauth_drive_stub: OAuthDriveStub,
    fake_rclone: FakeRclone,
    tmp_path: Path,
) -> None:
    oauth_drive_stub.state.existing_folder_id = "existing-folder-id"
    host_port = auth_port()
    container_path = PurePosixPath(str(tmp_path / "project"))
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

    callback = complete_oauth_flow(host_port)

    assert callback.status in {200, 302, 303}
    assert status_json(host_port)["mounted"] is True
    assert oauth_drive_stub.state.created_folders == []
    assert_rclone_config_precedes_mount(
        fake_rclone,
        remote_name="gdrive",
        folder_id="existing-folder-id",
        container_path=container_path,
    )


def test_logout_unmounts_and_clears_auth_config(
    oauth_drive_stub: OAuthDriveStub,
    fake_rclone: FakeRclone,
    tmp_path: Path,
) -> None:
    host_port = auth_port()
    container_path = PurePosixPath(str(tmp_path / "project"))
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
    callback = complete_oauth_flow(host_port)
    assert callback.status in {200, 302, 303}
    assert status_json(host_port)["mounted"] is True

    logout = read_url(service_url(host_port, "/logout"))

    assert logout.status == 200
    assert "text/html" in logout.headers.get("Content-Type", "")
    assert "<title>Connect Google Drive | Pet Project Cofounder</title>" in logout.text
    assert "Connect your Drive" in logout.text
    assert "Authorize Google Drive" in logout.text
    assert "Logged out." not in logout.text
    assert _style_block(logout.text) == _shared_page_style()
    authorize_url = extract_login_href(logout.text)
    assert authorize_url.startswith(oauth_drive_stub.authorize_url)
    status = status_json(host_port)
    assert status["state"] == "unauthenticated"
    assert status["authValid"] is False
    assert status["mounted"] is False
    assert fake_rclone.mount_marker.exists() is False
    assert fake_rclone.config_marker.exists() is False
    assert any(command[:1] == ["unmount"] for command in fake_rclone.commands())
    assert any(command[:2] == ["config", "delete"] for command in fake_rclone.commands())


def test_public_base_url_produces_public_redirect_uri_in_authorize_and_token_exchange(
    oauth_drive_stub: OAuthDriveStub,
    fake_rclone: FakeRclone,
    tmp_path: Path,
) -> None:
    host_port = auth_port()
    container_path = PurePosixPath(str(tmp_path / "project"))
    public_base_url = "https://notes.example.com"
    plugin = service_class()(
        host_port=host_port,
        drive_folder_name=oauth_drive_stub.state.expected_folder_name,
        container_path=container_path,
        oauth_authorize_url=oauth_drive_stub.authorize_url,
        oauth_token_url=oauth_drive_stub.token_url,
        drive_api_base_url=oauth_drive_stub.drive_api_base_url,
        public_base_url=public_base_url,
    )
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()
    start_plugin(plugin, container_spec.state, fake_rclone)

    login = read_url(service_url(host_port, "/login"))
    assert login.status == 200
    assert "text/html" in login.headers.get("Content-Type", "")

    authorize_url = extract_login_href(login.text)
    parsed = urllib.parse.urlparse(authorize_url)
    authorize_query = urllib.parse.parse_qs(parsed.query)
    expected_redirect = f"{public_base_url}/oauth/callback"
    assert authorize_query["redirect_uri"] == [expected_redirect], authorize_url

    assert status_json(host_port)["state"] == "authenticating"
    assert authorize_url.startswith(oauth_drive_stub.authorize_url)


def _style_block(page_html: str) -> str:
    return page_html.split("<style>\n", 1)[1].split("\n  </style>", 1)[0]


def _shared_page_style() -> str:
    return (
        resources.files("assistant_api.container_builder.container_plugin")
        .joinpath("assets", SHARED_STYLE_ASSET_NAME)
        .read_text(encoding="utf-8")
    )


def test_oauth_callback_selects_root_level_folder_when_multiple_folders_exist(
    oauth_drive_stub: OAuthDriveStub,
    fake_rclone: FakeRclone,
    tmp_path: Path,
) -> None:
    """Should select the root-level folder when there are multiple folders but only one root-level."""
    # Add a nested folder (non-root-level) and a root-level folder
    oauth_drive_stub.state.existing_folders = [
        {
            "id": "nested-folder-id",
            "name": oauth_drive_stub.state.expected_folder_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": ["some-other-parent-id"],  # Not root
        },
        {
            "id": "root-folder-id",
            "name": oauth_drive_stub.state.expected_folder_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [oauth_drive_stub.state.root_folder_id],  # Root
        },
    ]
    host_port = auth_port()
    container_path = PurePosixPath(str(tmp_path / "project"))
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

    callback = complete_oauth_flow(host_port)

    assert callback.status in {200, 302, 303}
    assert status_json(host_port)["mounted"] is True
    assert_rclone_config_precedes_mount(
        fake_rclone,
        remote_name="gdrive",
        folder_id="root-folder-id",
        container_path=container_path,
    )


def test_oauth_callback_fails_when_multiple_root_level_folders_exist(
    oauth_drive_stub: OAuthDriveStub,
    fake_rclone: FakeRclone,
    tmp_path: Path,
) -> None:
    """Should fail fast when multiple root-level folders with the same name exist."""
    oauth_drive_stub.state.existing_folders = [
        {
            "id": "root-folder-1",
            "name": oauth_drive_stub.state.expected_folder_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [oauth_drive_stub.state.root_folder_id],
        },
        {
            "id": "root-folder-2",
            "name": oauth_drive_stub.state.expected_folder_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [oauth_drive_stub.state.root_folder_id],
        },
    ]
    host_port = auth_port()
    container_path = PurePosixPath(str(tmp_path / "project"))
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

    callback = complete_oauth_flow(host_port)

    # Should fail with an error (500 or error page)
    assert callback.status == 500 or "error" in callback.text.lower()
    assert status_json(host_port)["state"] == "error"
    assert status_json(host_port)["mounted"] is False


def test_oauth_callback_ignores_nested_folders_and_selects_root_level(
    oauth_drive_stub: OAuthDriveStub,
    fake_rclone: FakeRclone,
    tmp_path: Path,
) -> None:
    """Should ignore nested folders and select the root-level folder."""
    # Only nested folders, no root-level folder -> should create a new one
    oauth_drive_stub.state.existing_folders = [
        {
            "id": "nested-folder-1",
            "name": oauth_drive_stub.state.expected_folder_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": ["some-other-parent-id"],
        },
        {
            "id": "nested-folder-2",
            "name": oauth_drive_stub.state.expected_folder_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": ["another-parent-id"],
        },
    ]
    host_port = auth_port()
    container_path = PurePosixPath(str(tmp_path / "project"))
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

    callback = complete_oauth_flow(host_port)

    assert callback.status in {200, 302, 303}
    assert status_json(host_port)["mounted"] is True
    # Should create a new folder since no root-level folder exists
    assert oauth_drive_stub.state.created_folders == [
        {
            "name": oauth_drive_stub.state.expected_folder_name,
            "mimeType": "application/vnd.google-apps.folder",
        }
    ]
    assert_rclone_config_precedes_mount(
        fake_rclone,
        remote_name="gdrive",
        folder_id=oauth_drive_stub.state.created_folder_id,
        container_path=container_path,
    )
