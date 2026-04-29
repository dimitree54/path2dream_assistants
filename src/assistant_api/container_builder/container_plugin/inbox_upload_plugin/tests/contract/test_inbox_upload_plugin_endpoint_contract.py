from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Iterator
from dataclasses import dataclass
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


@dataclass(slots=True)
class _InboxRuntime:
    builder: ContainerBuilderService
    host_port: int
    mount_dir: Path


@pytest.fixture(scope="module")
def _shared_inbox_runtime(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[_InboxRuntime]:
    runtime = _run_inbox_container(
        tmp_path_factory.mktemp("inbox-upload-shared"),
        unused_port(),
    )
    try:
        yield runtime
    finally:
        runtime.builder.stop(remove=True)


@pytest.fixture()
def inbox_runtime(_shared_inbox_runtime: _InboxRuntime) -> Iterator[_InboxRuntime]:
    _clear_directory(_shared_inbox_runtime.mount_dir / "inbox")
    yield _shared_inbox_runtime
    _clear_directory(_shared_inbox_runtime.mount_dir / "inbox")


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


def _clear_directory(path: Path) -> None:
    if not path.exists():
        return
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


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
) -> _InboxRuntime:
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
        container_name=f"notes-assistant-inbox-upload-test-{os.getpid()}-{host_port}",
    )
    builder.build_and_run()

    endpoint_path = upload_endpoint_path
    _wait_for_endpoint(_inbox_url(host_port, endpoint_path))

    return _InboxRuntime(builder=builder, host_port=host_port, mount_dir=mount_dir)


# ---------------------------------------------------------------------------
# Container image smoke tests
# ---------------------------------------------------------------------------


@pytest.mark.live_container
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


@pytest.mark.live_container
def test_upload_endpoint_accepts_file_and_returns_200_with_path_json(inbox_runtime: _InboxRuntime) -> None:
    host_port = inbox_runtime.host_port
    mount_dir = inbox_runtime.mount_dir
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


@pytest.mark.live_container
def test_upload_endpoint_saves_file_in_inbox_directory(inbox_runtime: _InboxRuntime) -> None:
    host_port = inbox_runtime.host_port
    mount_dir = inbox_runtime.mount_dir
    content = b"file content for saving test"
    inbox_dir = mount_dir / "inbox"

    response = _upload_file(
        _inbox_url(host_port),
        content,
        "save_test.txt",
    )

    assert response.status == 200
    saved_path = inbox_dir / "save_test.txt"
    assert saved_path.exists()
    assert saved_path.read_bytes() == content


@pytest.mark.live_container
def test_upload_endpoint_returns_absolute_container_path(inbox_runtime: _InboxRuntime) -> None:
    host_port = inbox_runtime.host_port
    mount_dir = inbox_runtime.mount_dir
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


@pytest.mark.live_container
def test_upload_endpoint_handles_text_file(inbox_runtime: _InboxRuntime) -> None:
    host_port = inbox_runtime.host_port
    mount_dir = inbox_runtime.mount_dir
    text_content = "Hello World!\nЭто текст на русском.\nLine 3."
    response = _upload_file(
        _inbox_url(host_port),
        text_content.encode("utf-8"),
        "text_utf8.txt",
    )

    assert response.status == 200
    saved_path = mount_dir / "inbox" / "text_utf8.txt"
    assert saved_path.read_text("utf-8") == text_content


@pytest.mark.live_container
def test_upload_endpoint_handles_binary_file(inbox_runtime: _InboxRuntime) -> None:
    host_port = inbox_runtime.host_port
    mount_dir = inbox_runtime.mount_dir
    binary_content = bytes(range(256))
    response = _upload_file(
        _inbox_url(host_port),
        binary_content,
        "binary.bin",
    )

    assert response.status == 200
    saved_path = mount_dir / "inbox" / "binary.bin"
    assert saved_path.read_bytes() == binary_content


@pytest.mark.live_container
def test_upload_endpoint_overwrites_existing_file(inbox_runtime: _InboxRuntime) -> None:
    host_port = inbox_runtime.host_port
    mount_dir = inbox_runtime.mount_dir
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

    saved_path = mount_dir / "inbox" / "overwrite.txt"
    assert saved_path.read_bytes() == b"second version"


@pytest.mark.live_container
def test_upload_endpoint_handles_multiple_sequential_uploads(inbox_runtime: _InboxRuntime) -> None:
    host_port = inbox_runtime.host_port
    mount_dir = inbox_runtime.mount_dir
    files = {
        "file_a.txt": b"content A",
        "file_b.txt": b"content B",
        "file_c.txt": b"content C",
    }
    for name, content in files.items():
        response = _upload_file(_inbox_url(host_port), content, name)
        assert response.status == 200

    for name, content in files.items():
        saved_path = mount_dir / "inbox" / name
        assert saved_path.exists()
        assert saved_path.read_bytes() == content


@pytest.mark.live_container
def test_upload_endpoint_handles_empty_file(inbox_runtime: _InboxRuntime) -> None:
    host_port = inbox_runtime.host_port
    mount_dir = inbox_runtime.mount_dir
    response = _upload_file(
        _inbox_url(host_port),
        b"",
        "empty.txt",
    )

    assert response.status == 200
    saved_path = mount_dir / "inbox" / "empty.txt"
    assert saved_path.exists()
    assert saved_path.read_bytes() == b""


# ---------------------------------------------------------------------------
# Upload endpoint filename handling
# ---------------------------------------------------------------------------


@pytest.mark.live_container
def test_upload_endpoint_handles_utf8_filename(inbox_runtime: _InboxRuntime) -> None:
    host_port = inbox_runtime.host_port
    mount_dir = inbox_runtime.mount_dir
    filename = "файл_на_русском.txt"
    response = _upload_file(
        _inbox_url(host_port),
        b"UTF-8 filename test",
        filename,
    )

    assert response.status == 200
    saved_path = mount_dir / "inbox" / filename
    assert saved_path.exists()


@pytest.mark.live_container
def test_upload_endpoint_handles_spaces_in_filename(inbox_runtime: _InboxRuntime) -> None:
    host_port = inbox_runtime.host_port
    mount_dir = inbox_runtime.mount_dir
    response = _upload_file(
        _inbox_url(host_port),
        b"spaces test",
        "my document.txt",
    )

    assert response.status == 200
    saved_path = mount_dir / "inbox" / "my document.txt"
    assert saved_path.exists()


@pytest.mark.live_container
def test_upload_endpoint_handles_special_chars_in_filename(inbox_runtime: _InboxRuntime) -> None:
    host_port = inbox_runtime.host_port
    mount_dir = inbox_runtime.mount_dir
    filename = "report_(2024)-v2.0_final+.txt"
    response = _upload_file(
        _inbox_url(host_port),
        b"special chars filename test",
        filename,
    )

    assert response.status == 200
    saved_path = mount_dir / "inbox" / filename
    assert saved_path.exists()


# ---------------------------------------------------------------------------
# Upload endpoint error handling
# ---------------------------------------------------------------------------


@pytest.mark.live_container
def test_upload_endpoint_rejects_missing_file_field(inbox_runtime: _InboxRuntime) -> None:
    host_port = inbox_runtime.host_port
    mount_dir = inbox_runtime.mount_dir
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


@pytest.mark.live_container
def test_upload_endpoint_rejects_path_traversal_filename(inbox_runtime: _InboxRuntime) -> None:
    host_port = inbox_runtime.host_port
    mount_dir = inbox_runtime.mount_dir
    response = _upload_file(
        _inbox_url(host_port),
        b"path traversal attempt",
        "../../../etc/passwd",
    )

    assert response.status == 400


@pytest.mark.live_container
def test_upload_endpoint_rejects_path_traversal_with_backslashes(inbox_runtime: _InboxRuntime) -> None:
    host_port = inbox_runtime.host_port
    mount_dir = inbox_runtime.mount_dir
    response = _upload_file(
        _inbox_url(host_port),
        b"path traversal with backslashes",
        "..\\..\\windows\\system32",
    )

    assert response.status == 400


@pytest.mark.live_container
def test_upload_endpoint_rejects_absolute_path_filename(inbox_runtime: _InboxRuntime) -> None:
    host_port = inbox_runtime.host_port
    mount_dir = inbox_runtime.mount_dir
    response = _upload_file(
        _inbox_url(host_port),
        b"absolute path attempt",
        "/etc/passwd",
    )

    assert response.status == 400


@pytest.mark.live_container
def test_upload_endpoint_get_request_returns_405(inbox_runtime: _InboxRuntime) -> None:
    host_port = inbox_runtime.host_port
    mount_dir = inbox_runtime.mount_dir
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


# ---------------------------------------------------------------------------
# Upload endpoint custom path
# ---------------------------------------------------------------------------


@pytest.mark.live_container
def test_upload_endpoint_respects_custom_upload_endpoint_path(tmp_path: Path) -> None:
    host_port = unused_port()
    custom_path = "/api/custom/upload-path"
    runtime = _run_inbox_container(
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
        runtime.builder.stop(remove=True)


# ---------------------------------------------------------------------------
# Upload endpoint concurrent uploads
# ---------------------------------------------------------------------------


@pytest.mark.live_container
def test_upload_endpoint_concurrent_uploads_do_not_corrupt(inbox_runtime: _InboxRuntime) -> None:
    host_port = inbox_runtime.host_port
    mount_dir = inbox_runtime.mount_dir
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

    inbox_dir = mount_dir / "inbox"
    for index in range(10):
        saved = inbox_dir / f"concurrent_{index}.txt"
        assert saved.exists()
        assert saved.read_bytes() == f"concurrent file {index}".encode()


# ---------------------------------------------------------------------------
# Upload endpoint file content fidelity
# ---------------------------------------------------------------------------


@pytest.mark.live_container
def test_upload_endpoint_preserves_exact_file_content(inbox_runtime: _InboxRuntime) -> None:
    host_port = inbox_runtime.host_port
    mount_dir = inbox_runtime.mount_dir
    size = 1024 * 1024  # 1 MB
    content = bytes(i % 256 for i in range(size))

    response = _upload_file(
        _inbox_url(host_port),
        content,
        "large_exact.bin",
        timeout=30,
    )

    assert response.status == 200
    saved_path = mount_dir / "inbox" / "large_exact.bin"
    assert saved_path.read_bytes() == content
