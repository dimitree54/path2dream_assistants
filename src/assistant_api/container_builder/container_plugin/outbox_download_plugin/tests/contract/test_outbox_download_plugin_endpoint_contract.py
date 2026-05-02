from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin.local_dir_mount_plugin import (
    LocalDirMountPluginService,
)
from outbox_download_contract_helpers import service_class, unused_port


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _OutboxRuntime:
    builder: ContainerBuilderService
    host_port: int
    mount_dir: Path


@pytest.fixture(scope="module")
def _shared_outbox_runtime(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[_OutboxRuntime]:
    runtime = _run_outbox_container(
        tmp_path_factory.mktemp("outbox-download-shared"),
        unused_port(),
    )
    try:
        yield runtime
    finally:
        runtime.builder.stop(remove=True)


@pytest.fixture()
def outbox_runtime(_shared_outbox_runtime: _OutboxRuntime) -> Iterator[_OutboxRuntime]:
    _clear_directory(_shared_outbox_runtime.mount_dir / "outbox")
    yield _shared_outbox_runtime
    _clear_directory(_shared_outbox_runtime.mount_dir / "outbox")


def _list_url(host_port: int, path: str = "/api/outbox/list") -> str:
    return f"http://127.0.0.1:{host_port}{path}"


def _download_url(host_port: int, filename: str, path: str = "/api/outbox/download") -> str:
    return f"http://127.0.0.1:{host_port}{path}/{quote(filename, safe='')}"


def _http_get(url: str, *, timeout: float = 15) -> _HttpResponse:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, method="GET")
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
            child.unlink(missing_ok=True)


def _wait_for_endpoint(url: str, timeout: float = 30) -> None:
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
    raise AssertionError(
        f"endpoint {url} did not become reachable within {timeout}s: {last_error}"
    )


def _wait_for_path_absent(path: Path, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not path.exists():
            return
        time.sleep(0.05)
    raise AssertionError(f"path still exists after {timeout}s: {path}")


def _wait_for_list_contains(
    host_port: int,
    expected_filenames: str | set[str],
    *,
    path: str = "/api/outbox/list",
    timeout: float = 5,
) -> _HttpResponse:
    expected = (
        {expected_filenames}
        if isinstance(expected_filenames, str)
        else expected_filenames
    )
    deadline = time.monotonic() + timeout
    last_response: _HttpResponse | None = None
    while time.monotonic() < deadline:
        response = _http_get(_list_url(host_port, path))
        last_response = response
        if response.status == 200 and expected <= set(response.json()):
            return response
        time.sleep(0.1)
    raise AssertionError(
        f"outbox list did not include {sorted(expected)!r} within {timeout}s: "
        f"{last_response.text if last_response is not None else '<no response>'}"
    )


def _wait_for_list_equals(
    host_port: int,
    expected_filenames: set[str],
    *,
    path: str = "/api/outbox/list",
    timeout: float = 5,
) -> _HttpResponse:
    deadline = time.monotonic() + timeout
    last_response: _HttpResponse | None = None
    while time.monotonic() < deadline:
        response = _http_get(_list_url(host_port, path))
        last_response = response
        if response.status == 200 and set(response.json()) == expected_filenames:
            return response
        time.sleep(0.1)
    raise AssertionError(
        f"outbox list did not equal {sorted(expected_filenames)!r} within {timeout}s: "
        f"{last_response.text if last_response is not None else '<no response>'}"
    )


def _wait_for_download(
    host_port: int,
    filename: str,
    *,
    path: str = "/api/outbox/download",
    timeout: float = 5,
) -> _HttpResponse:
    deadline = time.monotonic() + timeout
    last_response: _HttpResponse | None = None
    while time.monotonic() < deadline:
        response = _http_get(_download_url(host_port, filename, path))
        last_response = response
        if response.status != 404:
            return response
        time.sleep(0.1)
    raise AssertionError(
        f"outbox download did not find {filename!r} within {timeout}s: "
        f"{last_response.text if last_response is not None else '<no response>'}"
    )


def _run_outbox_container(
    tmp_path: Path,
    host_port: int,
    *,
    list_endpoint_path: str = "/api/outbox/list",
    download_endpoint_path: str = "/api/outbox/download",
    container_port: int | None = None,
) -> _OutboxRuntime:
    mount_dir = tmp_path / "mount"
    mount_dir.mkdir(parents=True, exist_ok=True)

    builder = ContainerBuilderService(
        plugins=[
            LocalDirMountPluginService(mount_dir),
            service_class()(
                host_port=host_port,
                container_port=container_port,
                list_endpoint_path=list_endpoint_path,
                download_endpoint_path=download_endpoint_path,
            ),
        ],
        container_name=f"notes-assistant-outbox-download-test-{os.getpid()}-{host_port}",
    )
    builder.build_and_run()

    _wait_for_endpoint(_list_url(host_port, list_endpoint_path))

    return _OutboxRuntime(builder=builder, host_port=host_port, mount_dir=mount_dir)


# ---------------------------------------------------------------------------
# Container image smoke tests
# ---------------------------------------------------------------------------


@pytest.mark.live_container
def test_container_image_builds_with_outbox_server_dependencies(
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
        container_name=(
            f"notes-assistant-outbox-download-build-test-{os.getpid()}"
        ),
    )

    try:
        builder.build()
    except Exception as error:
        pytest.fail(
            "outbox download plugin image must build before the endpoints can "
            f"run in a container; got {type(error).__name__}: {error}\n\n"
            f"{_docker_build_log(error)}"
        )


# ---------------------------------------------------------------------------
# List endpoint happy-path tests
# ---------------------------------------------------------------------------


@pytest.mark.live_container
def test_list_endpoint_returns_200_with_json_array(outbox_runtime: _OutboxRuntime) -> None:
    host_port = outbox_runtime.host_port
    mount_dir = outbox_runtime.mount_dir
    outbox_dir = mount_dir / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    (outbox_dir / "file1.txt").write_text("content 1")
    (outbox_dir / "file2.pdf").write_text("content 2")

    response = _wait_for_list_contains(host_port, {"file1.txt", "file2.pdf"})

    assert response.status == 200
    data = response.json()
    assert isinstance(data, list)


@pytest.mark.live_container
def test_list_endpoint_returns_empty_array_for_empty_outbox(outbox_runtime: _OutboxRuntime) -> None:
    host_port = outbox_runtime.host_port
    response = _wait_for_list_equals(host_port, set())

    assert response.status == 200
    data = response.json()
    assert data == []


@pytest.mark.live_container
def test_list_endpoint_returns_file_names(outbox_runtime: _OutboxRuntime) -> None:
    host_port = outbox_runtime.host_port
    mount_dir = outbox_runtime.mount_dir
    outbox_dir = mount_dir / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    (outbox_dir / "file1.txt").write_text("content 1")
    (outbox_dir / "file2.pdf").write_text("content 2")

    response = _wait_for_list_equals(host_port, {"file1.txt", "file2.pdf"})

    assert response.status == 200
    data = response.json()
    assert isinstance(data, list)
    assert sorted(data) == ["file1.txt", "file2.pdf"]


@pytest.mark.live_container
def test_list_endpoint_excludes_directories(outbox_runtime: _OutboxRuntime) -> None:
    host_port = outbox_runtime.host_port
    mount_dir = outbox_runtime.mount_dir
    outbox_dir = mount_dir / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    (outbox_dir / "myfile.txt").write_text("file")
    (outbox_dir / "subdir").mkdir()

    response = _wait_for_list_equals(host_port, {"myfile.txt"})

    assert response.status == 200
    data = response.json()
    assert "myfile.txt" in data
    assert "subdir" not in data


# ---------------------------------------------------------------------------
# List endpoint non-GET methods
# ---------------------------------------------------------------------------


@pytest.mark.live_container
def test_list_endpoint_rejects_post(outbox_runtime: _OutboxRuntime) -> None:
    host_port = outbox_runtime.host_port
    mount_dir = outbox_runtime.mount_dir
    import urllib.error
    import urllib.request

    request = urllib.request.Request(_list_url(host_port), method="POST", data=b"")
    try:
        urllib.request.urlopen(request, timeout=10)
        pytest.fail("expected HTTP error for POST request")
    except urllib.error.HTTPError as error:
        assert error.code == 405


@pytest.mark.live_container
def test_list_endpoint_rejects_put(outbox_runtime: _OutboxRuntime) -> None:
    host_port = outbox_runtime.host_port
    mount_dir = outbox_runtime.mount_dir
    import urllib.error
    import urllib.request

    request = urllib.request.Request(_list_url(host_port), method="PUT", data=b"")
    try:
        urllib.request.urlopen(request, timeout=10)
        pytest.fail("expected HTTP error for PUT request")
    except urllib.error.HTTPError as error:
        assert error.code == 405


@pytest.mark.live_container
def test_list_endpoint_rejects_delete(outbox_runtime: _OutboxRuntime) -> None:
    host_port = outbox_runtime.host_port
    mount_dir = outbox_runtime.mount_dir
    import urllib.error
    import urllib.request

    request = urllib.request.Request(_list_url(host_port), method="DELETE")
    try:
        urllib.request.urlopen(request, timeout=10)
        pytest.fail("expected HTTP error for DELETE request")
    except urllib.error.HTTPError as error:
        assert error.code == 405


# ---------------------------------------------------------------------------
# Download endpoint happy-path tests
# ---------------------------------------------------------------------------


@pytest.mark.live_container
def test_download_endpoint_returns_file_content(outbox_runtime: _OutboxRuntime) -> None:
    host_port = outbox_runtime.host_port
    mount_dir = outbox_runtime.mount_dir
    outbox_dir = mount_dir / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    content = b"Hello, outbox download!"
    (outbox_dir / "test.txt").write_bytes(content)

    response = _wait_for_download(host_port, "test.txt")

    assert response.status == 200
    assert response.body == content


@pytest.mark.live_container
def test_download_endpoint_deletes_file_after_successful_download(outbox_runtime: _OutboxRuntime) -> None:
    host_port = outbox_runtime.host_port
    mount_dir = outbox_runtime.mount_dir
    outbox_dir = mount_dir / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    (outbox_dir / "to_delete.txt").write_text("delete me")

    response = _wait_for_download(host_port, "to_delete.txt")

    assert response.status == 200
    _wait_for_path_absent(outbox_dir / "to_delete.txt")


@pytest.mark.live_container
def test_download_endpoint_handles_binary_file(outbox_runtime: _OutboxRuntime) -> None:
    host_port = outbox_runtime.host_port
    mount_dir = outbox_runtime.mount_dir
    outbox_dir = mount_dir / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    content = bytes(range(256))
    (outbox_dir / "binary.bin").write_bytes(content)

    response = _wait_for_download(host_port, "binary.bin")

    assert response.status == 200
    assert response.body == content


@pytest.mark.live_container
def test_download_endpoint_handles_empty_file(outbox_runtime: _OutboxRuntime) -> None:
    host_port = outbox_runtime.host_port
    mount_dir = outbox_runtime.mount_dir
    outbox_dir = mount_dir / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    (outbox_dir / "empty.txt").write_text("")

    response = _wait_for_download(host_port, "empty.txt")

    assert response.status == 200
    assert response.body == b""


@pytest.mark.live_container
def test_download_endpoint_handles_utf8_filename(outbox_runtime: _OutboxRuntime) -> None:
    host_port = outbox_runtime.host_port
    mount_dir = outbox_runtime.mount_dir
    outbox_dir = mount_dir / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    filename = "файл_на_русском.txt"
    content = b"UTF-8 filename download test"
    (outbox_dir / filename).write_bytes(content)

    response = _wait_for_download(host_port, filename)

    assert response.status == 200
    assert response.body == content


@pytest.mark.live_container
def test_download_endpoint_handles_spaces_in_filename(outbox_runtime: _OutboxRuntime) -> None:
    host_port = outbox_runtime.host_port
    mount_dir = outbox_runtime.mount_dir
    outbox_dir = mount_dir / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    filename = "my document.txt"
    content = b"spaces in filename"
    (outbox_dir / filename).write_bytes(content)

    response = _wait_for_download(host_port, filename)

    assert response.status == 200
    assert response.body == content


@pytest.mark.live_container
def test_download_endpoint_handles_special_chars_in_filename(outbox_runtime: _OutboxRuntime) -> None:
    host_port = outbox_runtime.host_port
    mount_dir = outbox_runtime.mount_dir
    outbox_dir = mount_dir / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    filename = "report_(2024)-v2.0_final+.txt"
    content = b"special chars filename test"
    (outbox_dir / filename).write_bytes(content)

    response = _wait_for_download(host_port, filename)

    assert response.status == 200
    assert response.body == content


@pytest.mark.live_container
def test_download_endpoint_no_size_limit_large_file(outbox_runtime: _OutboxRuntime) -> None:
    host_port = outbox_runtime.host_port
    mount_dir = outbox_runtime.mount_dir
    outbox_dir = mount_dir / "outbox"
    outbox_dir.mkdir(parents=True, exist_ok=True)
    size = 1024 * 1024  # 1 MB
    content = bytes(i % 256 for i in range(size))
    (outbox_dir / "large.bin").write_bytes(content)

    response = _wait_for_download(host_port, "large.bin", timeout=30)

    assert response.status == 200
    assert len(response.body) == size
    assert response.body == content


# ---------------------------------------------------------------------------
# Download endpoint error handling
# ---------------------------------------------------------------------------


@pytest.mark.live_container
def test_download_endpoint_returns_404_for_missing_file(outbox_runtime: _OutboxRuntime) -> None:
    host_port = outbox_runtime.host_port
    mount_dir = outbox_runtime.mount_dir
    response = _http_get(_download_url(host_port, "nonexistent.txt"))

    assert response.status == 404


@pytest.mark.live_container
def test_download_endpoint_rejects_path_traversal(outbox_runtime: _OutboxRuntime) -> None:
    host_port = outbox_runtime.host_port
    mount_dir = outbox_runtime.mount_dir
    response = _http_get(_download_url(host_port, "../../../etc/passwd"))

    assert response.status == 400


@pytest.mark.live_container
def test_download_endpoint_rejects_path_traversal_with_backslashes(outbox_runtime: _OutboxRuntime) -> None:
    host_port = outbox_runtime.host_port
    mount_dir = outbox_runtime.mount_dir
    response = _http_get(_download_url(host_port, "..\\..\\windows\\system32"))

    assert response.status == 400


@pytest.mark.live_container
def test_download_endpoint_rejects_absolute_path(outbox_runtime: _OutboxRuntime) -> None:
    host_port = outbox_runtime.host_port
    mount_dir = outbox_runtime.mount_dir
    response = _http_get(_download_url(host_port, "/etc/passwd"))

    assert response.status == 400


# ---------------------------------------------------------------------------
# Download endpoint non-GET methods
# ---------------------------------------------------------------------------


@pytest.mark.live_container
def test_download_endpoint_rejects_post(outbox_runtime: _OutboxRuntime) -> None:
    host_port = outbox_runtime.host_port
    mount_dir = outbox_runtime.mount_dir
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        _download_url(host_port, "somefile.txt"),
        method="POST",
        data=b"",
    )
    try:
        urllib.request.urlopen(request, timeout=10)
        pytest.fail("expected HTTP error for POST request")
    except urllib.error.HTTPError as error:
        assert error.code == 405


@pytest.mark.live_container
def test_download_endpoint_rejects_put(outbox_runtime: _OutboxRuntime) -> None:
    host_port = outbox_runtime.host_port
    mount_dir = outbox_runtime.mount_dir
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        _download_url(host_port, "somefile.txt"),
        method="PUT",
        data=b"",
    )
    try:
        urllib.request.urlopen(request, timeout=10)
        pytest.fail("expected HTTP error for PUT request")
    except urllib.error.HTTPError as error:
        assert error.code == 405


@pytest.mark.live_container
def test_download_endpoint_rejects_delete(outbox_runtime: _OutboxRuntime) -> None:
    host_port = outbox_runtime.host_port
    mount_dir = outbox_runtime.mount_dir
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        _download_url(host_port, "somefile.txt"),
        method="DELETE",
    )
    try:
        urllib.request.urlopen(request, timeout=10)
        pytest.fail("expected HTTP error for DELETE request")
    except urllib.error.HTTPError as error:
        assert error.code == 405


# ---------------------------------------------------------------------------
# Custom endpoint paths
# ---------------------------------------------------------------------------


@pytest.mark.live_container
def test_list_endpoint_respects_custom_path(tmp_path: Path) -> None:
    host_port = unused_port()
    custom_list = "/api/custom/outbox-list"
    runtime = _run_outbox_container(
        tmp_path,
        host_port,
        list_endpoint_path=custom_list,
    )
    try:
        outbox_dir = tmp_path / "mount" / "outbox"
        outbox_dir.mkdir(parents=True, exist_ok=True)
        (outbox_dir / "test.txt").write_text("custom list path")

        response = _wait_for_list_contains(
            host_port,
            "test.txt",
            path=custom_list,
        )

        assert response.status == 200
        data = response.json()
        assert "test.txt" in data
    finally:
        runtime.builder.stop(remove=True)


@pytest.mark.live_container
def test_download_endpoint_respects_custom_path(tmp_path: Path) -> None:
    host_port = unused_port()
    custom_download = "/api/custom/outbox-download"
    runtime = _run_outbox_container(
        tmp_path,
        host_port,
        download_endpoint_path=custom_download,
    )
    try:
        outbox_dir = tmp_path / "mount" / "outbox"
        outbox_dir.mkdir(parents=True, exist_ok=True)
        content = b"custom download path"
        (outbox_dir / "custom.txt").write_bytes(content)

        response = _wait_for_download(
            host_port,
            "custom.txt",
            path=custom_download,
        )

        assert response.status == 200
        assert response.body == content
        _wait_for_path_absent(outbox_dir / "custom.txt")
    finally:
        runtime.builder.stop(remove=True)
