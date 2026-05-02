from __future__ import annotations

from pathlib import Path, PurePosixPath

from assistant_api.container_builder import ContainerBuilderService
from google_drive_mount_contract_helpers import auth_port, read_url, service_class, service_url
from google_drive_mount_oauth_stub import OAuthDriveStub, google_env, oauth_drive_stub
from google_drive_mount_rclone_stub import FakeRclone, fake_rclone, start_plugin


def test_mounted_success_page_hides_local_folder_import_by_default() -> None:
    from assistant_api.container_builder.container_plugin.google_drive_mount_plugin import _login_page

    html = _login_page.render_mount_success_page("notes")

    assert "/import/local-folder" not in html
    assert "webkitdirectory" not in html
    assert "Choose local folder" not in html
    assert "Choose files from Google Drive" not in html


def test_mounted_success_page_shows_local_folder_import_when_enabled() -> None:
    from assistant_api.container_builder.container_plugin.google_drive_mount_plugin import _login_page

    html = _login_page.render_mount_success_page("notes", enable_local_folder_import=True)

    assert html.index("Google Drive is mounted") < html.index("Import notes") < html.index("Log out")
    assert 'action="/import/local-folder"' in html
    assert 'type="file"' in html
    assert 'data-import-files-button' in html
    assert 'data-import-folder-button' in html
    assert "webkitdirectory" in html
    assert "webkitRelativePath" in html
    assert "multiple" in html
    assert "data-import-choice-panel" in html
    assert "data-import-selection-panel" in html
    assert "data-import-progress-panel" in html
    assert "data-import-result-panel" in html
    assert "Choose files" in html
    assert "Choose folder" in html
    assert "Choose different files" in html
    assert "Choose different folder" in html
    assert "Choose files or a folder to import notes." in html
    assert "Nothing will be copied until you choose what to import." in html
    assert "Ready to import" in html
    assert "Uploading files to the app" in html
    assert "Copying files into your Drive folder" in html
    assert "You can return to the Assistant or import more." in html
    assert "Import more" in html
    assert "Try import again" in html
    assert "Create selected folder" in html
    assert "Import folder contents" in html
    assert 'data-import-selection-panel data-import-state="selected" aria-live="polite" hidden' in html
    assert 'data-import-progress-panel data-import-state="uploading" aria-live="polite" hidden' in html
    assert 'data-import-result-panel data-import-state="success" aria-live="polite" hidden' in html
    assert 'class="import-folder-mode hidden" data-folder-mode-panel hidden' in html
    assert "data-folder-mode-panel" in html
    assert 'data-import-submit disabled' not in html
    assert "create-folder" in html
    assert "strip-folder" in html
    assert "Choose files from Google Drive" not in html


def test_local_folder_import_page_script_hides_irrelevant_controls_by_state() -> None:
    from assistant_api.container_builder.container_plugin.google_drive_mount_plugin import _login_page

    html = _login_page.render_mount_success_page("notes", enable_local_folder_import=True)

    assert 'setHidden(choicePanel, state !== "empty")' in html
    assert "setHidden(selectionPanel, !isSelectedState)" in html
    assert "setHidden(progressPanel, !isBusy)" in html
    assert 'setHidden(resultPanel, state !== "success")' in html
    assert (
        'setHidden(folderModePanel, !(isSelectedState && selectedSource === "folder"))'
        in html
    )
    assert 'submitButton.textContent = state === "error" ? "Try import again" : "Import"' in html
    assert 'rechooseButton.textContent = selectedSource === "folder" ? "Choose different folder" : "Choose different files"' in html
    assert 'rechooseButton.addEventListener("click", resetImportFlow)' in html
    assert 'importMoreButton.addEventListener("click", resetImportFlow)' in html
    assert "selectedFiles = [];" in html
    assert "selectedSource = null;" in html
    assert 'renderImportState("uploading")' in html
    assert '"finalizing"' in html


def test_local_folder_import_page_script_shapes_target_paths() -> None:
    from assistant_api.container_builder.container_plugin.google_drive_mount_plugin import _login_page

    html = _login_page.render_mount_success_page("notes", enable_local_folder_import=True)

    assert "data-source-mode" in html
    assert "file.name" in html
    assert "file.webkitRelativePath" in html
    assert "strip-folder" in html
    assert "relativePath.split(\"/\").slice(1).join(\"/\")" in html
    assert "formData.append(\"files\", file, targetPath)" in html
    assert "folderInput.value = \"\";" in html
    assert "fileInput.value = \"\";" in html


def test_local_folder_import_rejects_requests_before_google_drive_is_mounted(
    google_env: str,
    fake_rclone: FakeRclone,
    tmp_path: Path,
) -> None:
    host_port = auth_port()
    plugin = service_class()(
        host_port=host_port,
        drive_folder_name=google_env,
        container_path=PurePosixPath(str(tmp_path / "project")),
        enable_local_folder_import=True,
    )
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()
    start_plugin(plugin, container_spec.state, fake_rclone)

    response = _post_local_folder_import(host_port, {"note.md": b"draft"})

    assert response.status == 409
    assert "mounted" in response.text.lower()


def test_local_folder_import_recursively_copies_files_into_mounted_root(
    oauth_drive_stub: OAuthDriveStub,
    fake_rclone: FakeRclone,
    tmp_path: Path,
) -> None:
    host_port, mount_path = _mounted_import_runtime(oauth_drive_stub, fake_rclone, tmp_path)

    response = _post_local_folder_import(
        host_port,
        {
            "root.md": b"root note",
            "nested/deep.md": b"deep note",
        },
    )

    assert response.status == 200, response.text
    assert (mount_path / "root.md").read_bytes() == b"root note"
    assert (mount_path / "nested" / "deep.md").read_bytes() == b"deep note"


def test_local_folder_import_copies_individual_files_into_mounted_root(
    oauth_drive_stub: OAuthDriveStub,
    fake_rclone: FakeRclone,
    tmp_path: Path,
) -> None:
    host_port, mount_path = _mounted_import_runtime(oauth_drive_stub, fake_rclone, tmp_path)

    response = _post_local_folder_import(
        host_port,
        {
            "first.md": b"first note",
            "second.txt": b"second note",
        },
    )

    assert response.status == 200, response.text
    assert (mount_path / "first.md").read_bytes() == b"first note"
    assert (mount_path / "second.txt").read_bytes() == b"second note"


def test_local_folder_import_preserves_selected_folder_parent_path(
    oauth_drive_stub: OAuthDriveStub,
    fake_rclone: FakeRclone,
    tmp_path: Path,
) -> None:
    host_port, mount_path = _mounted_import_runtime(oauth_drive_stub, fake_rclone, tmp_path)

    response = _post_local_folder_import(
        host_port,
        {
            "MyNotes/root.md": b"root note",
            "MyNotes/nested/deep.md": b"deep note",
        },
    )

    assert response.status == 200, response.text
    assert (mount_path / "MyNotes" / "root.md").read_bytes() == b"root note"
    assert (mount_path / "MyNotes" / "nested" / "deep.md").read_bytes() == b"deep note"


def test_local_folder_import_supports_selected_folder_contents_path_shape(
    oauth_drive_stub: OAuthDriveStub,
    fake_rclone: FakeRclone,
    tmp_path: Path,
) -> None:
    host_port, mount_path = _mounted_import_runtime(oauth_drive_stub, fake_rclone, tmp_path)

    response = _post_local_folder_import(
        host_port,
        {
            "root.md": b"root note",
            "nested/deep.md": b"deep note",
        },
    )

    assert response.status == 200, response.text
    assert (mount_path / "root.md").read_bytes() == b"root note"
    assert (mount_path / "nested" / "deep.md").read_bytes() == b"deep note"
    assert not (mount_path / "MyNotes").exists()


def test_local_folder_import_fails_without_overwriting_existing_files(
    oauth_drive_stub: OAuthDriveStub,
    fake_rclone: FakeRclone,
    tmp_path: Path,
) -> None:
    host_port, mount_path = _mounted_import_runtime(oauth_drive_stub, fake_rclone, tmp_path)
    existing = mount_path / "nested" / "note.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("original", encoding="utf-8")

    response = _post_local_folder_import(
        host_port,
        {
            "nested/note.md": b"replacement",
            "new.md": b"new content",
        },
    )

    assert response.status == 409
    assert "already exists" in response.text.lower()
    assert existing.read_text(encoding="utf-8") == "original"
    assert not (mount_path / "new.md").exists()


def test_local_folder_import_fails_without_partial_writes_when_parent_is_file(
    oauth_drive_stub: OAuthDriveStub,
    fake_rclone: FakeRclone,
    tmp_path: Path,
) -> None:
    host_port, mount_path = _mounted_import_runtime(oauth_drive_stub, fake_rclone, tmp_path)
    existing_parent = mount_path / "blocked"
    existing_parent.write_text("not a directory", encoding="utf-8")

    response = _post_local_folder_import(
        host_port,
        {
            "new.md": b"new content",
            "blocked/note.md": b"nested content",
        },
    )

    assert response.status == 409
    assert "already exists" in response.text.lower()
    assert existing_parent.read_text(encoding="utf-8") == "not a directory"
    assert not (mount_path / "new.md").exists()


def test_local_folder_import_rejects_unsafe_relative_paths(
    oauth_drive_stub: OAuthDriveStub,
    fake_rclone: FakeRclone,
    tmp_path: Path,
) -> None:
    host_port, mount_path = _mounted_import_runtime(oauth_drive_stub, fake_rclone, tmp_path)

    for relative_path in ("../escape.md", "/absolute.md", "nested/../escape.md", ""):
        response = _post_local_folder_import(host_port, {relative_path: b"bad"})

        assert response.status == 400
        assert "path" in response.text.lower()

    assert not (mount_path.parent / "escape.md").exists()
    assert not (mount_path / "absolute.md").exists()


def _mounted_import_runtime(
    oauth_drive_stub: OAuthDriveStub,
    fake_rclone: FakeRclone,
    tmp_path: Path,
) -> tuple[int, Path]:
    from google_drive_mount_contract_helpers import complete_oauth_flow

    host_port = auth_port()
    mount_path = tmp_path / "project"
    plugin = service_class()(
        host_port=host_port,
        drive_folder_name=oauth_drive_stub.state.expected_folder_name,
        container_path=PurePosixPath(str(mount_path)),
        oauth_authorize_url=oauth_drive_stub.authorize_url,
        oauth_token_url=oauth_drive_stub.token_url,
        drive_api_base_url=oauth_drive_stub.drive_api_base_url,
        enable_local_folder_import=True,
    )
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()
    start_plugin(plugin, container_spec.state, fake_rclone)
    callback = complete_oauth_flow(host_port)
    assert callback.status in {200, 302, 303}, callback.text
    return host_port, mount_path


def _post_local_folder_import(
    host_port: int,
    files: dict[str, bytes],
):
    boundary = "----LocalFolderImportBoundary"
    body_parts: list[bytes] = []
    for relative_path, content in files.items():
        body_parts.append(f"--{boundary}".encode())
        body_parts.append(
            (
                'Content-Disposition: form-data; name="files"; '
                f'filename="{relative_path}"'
            ).encode()
        )
        body_parts.append(b"Content-Type: application/octet-stream")
        body_parts.append(b"")
        body_parts.append(content)
    body_parts.append(f"--{boundary}--".encode())
    body = b"\r\n".join(body_parts)
    return read_url(
        service_url(host_port, "/import/local-folder"),
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
