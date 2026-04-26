from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin.local_dir_mount_plugin import (
    LocalDirMountPluginService,
)
from inbox_upload_contract_helpers import service_class, unused_port


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _inbox_url(host_port: int, path: str = "/api/inbox/upload") -> str:
    return f"http://127.0.0.1:{host_port}{path}"


def _upload_file(
    url: str,
    file_content: bytes,
    filename: str,
    content_type: str = "application/octet-stream",
    *,
    timeout: float = 15,
) -> _HttpResponse:
    import urllib.error
    import urllib.request

    boundary = "----TestBoundary12345"
    body_lines: list[bytes] = []
    body_lines.append(f"--{boundary}".encode())
    body_lines.append(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"'.encode()
    )
    body_lines.append(f"Content-Type: {content_type}".encode())
    body_lines.append(b"")
    body_lines.append(file_content)
    body_lines.append(f"--{boundary}--".encode())
    body = b"\r\n".join(body_lines)

    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return _HttpResponse(
                status=response.status,
                body=response.read(),
            )
    except urllib.error.HTTPError as error:
        return _HttpResponse(
            status=error.code,
            body=error.read(),
        )


class _HttpResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")

    def json(self) -> object:
        return json.loads(self.text)


def _docker_build_log(error: BaseException) -> str:
    build_log = getattr(error, "build_log", None)
    if not build_log:
        return "<docker build log is not available>"

    lines = []
    for entry in build_log:
        line = entry.get("stream") or entry.get("error") or repr(entry)
        lines.append(line.rstrip())
    return "\n".join(lines)


def _wait_for_endpoint(url: str, timeout: float = 30) -> None:
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except urllib.error.HTTPError as exc:
            if exc.code == 405:
                return
            last_error = exc
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
    raise AssertionError(
        f"endpoint {url} did not become reachable within {timeout}s: {last_error}"
    )


def _run_inbox_container(
    tmp_path: Path,
    host_port: int,
    *,
    upload_endpoint_path: str = "/api/inbox/upload",
    container_port: int | None = None,
) -> ContainerBuilderService:
    mount_dir = tmp_path / "mount"
    mount_dir.mkdir(parents=True, exist_ok=True)

    builder = ContainerBuilderService(
        plugins=[
            LocalDirMountPluginService(mount_dir),
            service_class()(
                host_port=host_port,
                container_port=container_port,
                upload_endpoint_path=upload_endpoint_path,
            ),
        ],
        container_name=f"notes-assistant-inbox-upload-test-{os.getpid()}",
    )
    builder.build_and_run()

    endpoint_path = upload_endpoint_path
    _wait_for_endpoint(_inbox_url(host_port, endpoint_path))

    return builder


# ---------------------------------------------------------------------------
# Container image smoke tests
# ---------------------------------------------------------------------------


@pytest.mark.manual
def test_container_image_builds_with_inbox_server_dependencies(
    tmp_path: Path,
) -> None:
    host_port = unused_port()
    mount_dir = tmp_path / "mount"
    mount_dir.mkdir(parents=True, exist_ok=True)

    builder = ContainerBuilderService(
        plugins=[
            LocalDirMountPluginService(mount_dir),
            service_class()(host_port=host_port),
        ],
        container_name=f"notes-assistant-inbox-upload-build-test-{os.getpid()}",
    )

    try:
        builder.build()
    except Exception as error:
        pytest.fail(
            "inbox upload plugin image must build before the endpoint can "
            f"run in a container; got {type(error).__name__}: {error}\n\n"
            f"{_docker_build_log(error)}"
        )


# ---------------------------------------------------------------------------
# Upload endpoint happy-path tests
# ---------------------------------------------------------------------------


@pytest.mark.manual
def test_upload_endpoint_accepts_file_and_returns_200_with_path_json(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_inbox_container(tmp_path, host_port)
    try:
        response = _upload_file(
            _inbox_url(host_port),
            b"Hello, inbox!",
            "hello.txt",
        )

        assert response.status == 200
        data = response.json()
        assert isinstance(data, dict)
        assert "path" in data
        assert isinstance(data["path"], str)
        assert data["path"].endswith("/inbox/hello.txt")
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_upload_endpoint_saves_file_in_inbox_directory(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_inbox_container(tmp_path, host_port)
    try:
        content = b"file content for saving test"
        response = _upload_file(
            _inbox_url(host_port),
            content,
            "save_test.txt",
        )

        assert response.status == 200
        saved_path = tmp_path / "mount" / "inbox" / "save_test.txt"
        assert saved_path.exists()
        assert saved_path.read_bytes() == content
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_upload_endpoint_returns_absolute_container_path(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_inbox_container(tmp_path, host_port)
    try:
        response = _upload_file(
            _inbox_url(host_port),
            b"path test",
            "path_test.txt",
        )

        assert response.status == 200
        data = response.json()
        path_value = data["path"]
        assert path_value.startswith("/")
        assert path_value.endswith("/inbox/path_test.txt")
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_upload_endpoint_handles_text_file(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_inbox_container(tmp_path, host_port)
    try:
        text_content = "Hello World!\nЭто текст на русском.\nLine 3."
        response = _upload_file(
            _inbox_url(host_port),
            text_content.encode("utf-8"),
            "text_utf8.txt",
        )

        assert response.status == 200
        saved_path = tmp_path / "mount" / "inbox" / "text_utf8.txt"
        assert saved_path.read_text("utf-8") == text_content
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_upload_endpoint_handles_binary_file(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_inbox_container(tmp_path, host_port)
    try:
        binary_content = bytes(range(256))
        response = _upload_file(
            _inbox_url(host_port),
            binary_content,
            "binary.bin",
        )

        assert response.status == 200
        saved_path = tmp_path / "mount" / "inbox" / "binary.bin"
        assert saved_path.read_bytes() == binary_content
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_upload_endpoint_overwrites_existing_file(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_inbox_container(tmp_path, host_port)
    try:
        first = _upload_file(
            _inbox_url(host_port),
            b"first version",
            "overwrite.txt",
        )
        assert first.status == 200

        second = _upload_file(
            _inbox_url(host_port),
            b"second version",
            "overwrite.txt",
        )
        assert second.status == 200

        saved_path = tmp_path / "mount" / "inbox" / "overwrite.txt"
        assert saved_path.read_bytes() == b"second version"
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_upload_endpoint_handles_multiple_sequential_uploads(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_inbox_container(tmp_path, host_port)
    try:
        files = {
            "file_a.txt": b"content A",
            "file_b.txt": b"content B",
            "file_c.txt": b"content C",
        }
        for name, content in files.items():
            response = _upload_file(_inbox_url(host_port), content, name)
            assert response.status == 200

        for name, content in files.items():
            saved_path = tmp_path / "mount" / "inbox" / name
            assert saved_path.exists()
            assert saved_path.read_bytes() == content
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_upload_endpoint_handles_empty_file(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_inbox_container(tmp_path, host_port)
    try:
        response = _upload_file(
            _inbox_url(host_port),
            b"",
            "empty.txt",
        )

        assert response.status == 200
        saved_path = tmp_path / "mount" / "inbox" / "empty.txt"
        assert saved_path.exists()
        assert saved_path.read_bytes() == b""
    finally:
        builder.stop(remove=True)


# ---------------------------------------------------------------------------
# Upload endpoint filename handling
# ---------------------------------------------------------------------------


@pytest.mark.manual
def test_upload_endpoint_handles_utf8_filename(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_inbox_container(tmp_path, host_port)
    try:
        filename = "файл_на_русском.txt"
        response = _upload_file(
            _inbox_url(host_port),
            b"UTF-8 filename test",
            filename,
        )

        assert response.status == 200
        saved_path = tmp_path / "mount" / "inbox" / filename
        assert saved_path.exists()
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_upload_endpoint_handles_spaces_in_filename(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_inbox_container(tmp_path, host_port)
    try:
        response = _upload_file(
            _inbox_url(host_port),
            b"spaces test",
            "my document.txt",
        )

        assert response.status == 200
        saved_path = tmp_path / "mount" / "inbox" / "my document.txt"
        assert saved_path.exists()
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_upload_endpoint_handles_special_chars_in_filename(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_inbox_container(tmp_path, host_port)
    try:
        filename = "report_(2024)-v2.0_final+.txt"
        response = _upload_file(
            _inbox_url(host_port),
            b"special chars filename test",
            filename,
        )

        assert response.status == 200
        saved_path = tmp_path / "mount" / "inbox" / filename
        assert saved_path.exists()
    finally:
        builder.stop(remove=True)


# ---------------------------------------------------------------------------
# Upload endpoint error handling
# ---------------------------------------------------------------------------


@pytest.mark.manual
def test_upload_endpoint_rejects_missing_file_field(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_inbox_container(tmp_path, host_port)
    try:
        import urllib.error
        import urllib.request

        boundary = "----TestBoundary"
        body = f"--{boundary}\r\nContent-Disposition: form-data; name=\"other\"\r\n\r\nvalue\r\n--{boundary}--\r\n".encode()

        request = urllib.request.Request(
            _inbox_url(host_port),
            data=body,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            urllib.request.urlopen(request, timeout=10)
            pytest.fail("expected HTTP error")
        except urllib.error.HTTPError as error:
            assert error.code >= 400
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_upload_endpoint_rejects_path_traversal_filename(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_inbox_container(tmp_path, host_port)
    try:
        response = _upload_file(
            _inbox_url(host_port),
            b"path traversal attempt",
            "../../../etc/passwd",
        )

        assert response.status == 400
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_upload_endpoint_rejects_path_traversal_with_backslashes(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_inbox_container(tmp_path, host_port)
    try:
        response = _upload_file(
            _inbox_url(host_port),
            b"path traversal with backslashes",
            "..\\..\\windows\\system32",
        )

        assert response.status == 400
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_upload_endpoint_rejects_absolute_path_filename(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_inbox_container(tmp_path, host_port)
    try:
        response = _upload_file(
            _inbox_url(host_port),
            b"absolute path attempt",
            "/etc/passwd",
        )

        assert response.status == 400
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_upload_endpoint_get_request_returns_405(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_inbox_container(tmp_path, host_port)
    try:
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            _inbox_url(host_port),
            method="GET",
        )
        try:
            urllib.request.urlopen(request, timeout=10)
            pytest.fail("expected HTTP error for GET request")
        except urllib.error.HTTPError as error:
            assert error.code == 405
    finally:
        builder.stop(remove=True)


# ---------------------------------------------------------------------------
# Upload endpoint custom path
# ---------------------------------------------------------------------------


@pytest.mark.manual
def test_upload_endpoint_respects_custom_upload_endpoint_path(tmp_path: Path) -> None:
    host_port = unused_port()
    custom_path = "/api/custom/upload-path"
    builder = _run_inbox_container(
        tmp_path,
        host_port,
        upload_endpoint_path=custom_path,
    )
    try:
        response = _upload_file(
            _inbox_url(host_port, custom_path),
            b"custom path test",
            "custom.txt",
        )

        assert response.status == 200
        assert response.json()["path"].endswith("/inbox/custom.txt")
    finally:
        builder.stop(remove=True)


# ---------------------------------------------------------------------------
# Upload endpoint concurrent uploads
# ---------------------------------------------------------------------------


@pytest.mark.manual
def test_upload_endpoint_concurrent_uploads_do_not_corrupt(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_inbox_container(tmp_path, host_port)
    try:
        import concurrent.futures

        def upload_one(index: int) -> None:
            content = f"concurrent file {index}".encode()
            filename = f"concurrent_{index}.txt"
            response = _upload_file(
                _inbox_url(host_port),
                content,
                filename,
                timeout=30,
            )
            assert response.status == 200

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            list(executor.map(upload_one, range(10)))

        inbox_dir = tmp_path / "mount" / "inbox"
        for index in range(10):
            saved = inbox_dir / f"concurrent_{index}.txt"
            assert saved.exists()
            assert saved.read_bytes() == f"concurrent file {index}".encode()
    finally:
        builder.stop(remove=True)


# ---------------------------------------------------------------------------
# Upload endpoint file content fidelity
# ---------------------------------------------------------------------------


@pytest.mark.manual
def test_upload_endpoint_preserves_exact_file_content(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_inbox_container(tmp_path, host_port)
    try:
        size = 1024 * 1024  # 1 MB
        content = bytes(i % 256 for i in range(size))

        response = _upload_file(
            _inbox_url(host_port),
            content,
            "large_exact.bin",
            timeout=30,
        )

        assert response.status == 200
        saved_path = tmp_path / "mount" / "inbox" / "large_exact.bin"
        assert saved_path.read_bytes() == content
    finally:
        builder.stop(remove=True)
