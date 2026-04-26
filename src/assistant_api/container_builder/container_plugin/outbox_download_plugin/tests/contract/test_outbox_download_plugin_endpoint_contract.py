from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin.local_dir_mount_plugin import (
    LocalDirMountPluginService,
)
from outbox_download_contract_helpers import service_class, unused_port


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _list_url(host_port: int, path: str = "/api/outbox/list") -> str:
    return f"http://127.0.0.1:{host_port}{path}"


def _download_url(host_port: int, filename: str, path: str = "/api/outbox/download") -> str:
    return f"http://127.0.0.1:{host_port}{path}/{filename}"


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


def _run_outbox_container(
    tmp_path: Path,
    host_port: int,
    *,
    list_endpoint_path: str = "/api/outbox/list",
    download_endpoint_path: str = "/api/outbox/download",
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
                list_endpoint_path=list_endpoint_path,
                download_endpoint_path=download_endpoint_path,
            ),
        ],
        container_name=f"notes-assistant-outbox-download-test-{os.getpid()}",
    )
    builder.build_and_run()

    _wait_for_endpoint(_list_url(host_port, list_endpoint_path))

    return builder


# ---------------------------------------------------------------------------
# List endpoint happy-path tests
# ---------------------------------------------------------------------------


@pytest.mark.manual
def test_list_endpoint_returns_200_with_json_array(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_outbox_container(tmp_path, host_port)
    try:
        response = _http_get(_list_url(host_port))

        assert response.status == 200
        data = response.json()
        assert isinstance(data, list)
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_list_endpoint_returns_empty_array_for_empty_outbox(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_outbox_container(tmp_path, host_port)
    try:
        response = _http_get(_list_url(host_port))

        assert response.status == 200
        data = response.json()
        assert data == []
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_list_endpoint_returns_file_names(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_outbox_container(tmp_path, host_port)
    try:
        outbox_dir = tmp_path / "mount" / "outbox"
        outbox_dir.mkdir(parents=True, exist_ok=True)
        (outbox_dir / "file1.txt").write_text("content 1")
        (outbox_dir / "file2.pdf").write_text("content 2")

        response = _http_get(_list_url(host_port))

        assert response.status == 200
        data = response.json()
        assert isinstance(data, list)
        assert sorted(data) == ["file1.txt", "file2.pdf"]
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_list_endpoint_excludes_directories(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_outbox_container(tmp_path, host_port)
    try:
        outbox_dir = tmp_path / "mount" / "outbox"
        outbox_dir.mkdir(parents=True, exist_ok=True)
        (outbox_dir / "myfile.txt").write_text("file")
        (outbox_dir / "subdir").mkdir()

        response = _http_get(_list_url(host_port))

        assert response.status == 200
        data = response.json()
        assert "myfile.txt" in data
        assert "subdir" not in data
    finally:
        builder.stop(remove=True)


# ---------------------------------------------------------------------------
# List endpoint non-GET methods
# ---------------------------------------------------------------------------


@pytest.mark.manual
def test_list_endpoint_rejects_post(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_outbox_container(tmp_path, host_port)
    try:
        import urllib.error
        import urllib.request

        request = urllib.request.Request(_list_url(host_port), method="POST", data=b"")
        try:
            urllib.request.urlopen(request, timeout=10)
            pytest.fail("expected HTTP error for POST request")
        except urllib.error.HTTPError as error:
            assert error.code == 405
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_list_endpoint_rejects_put(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_outbox_container(tmp_path, host_port)
    try:
        import urllib.error
        import urllib.request

        request = urllib.request.Request(_list_url(host_port), method="PUT", data=b"")
        try:
            urllib.request.urlopen(request, timeout=10)
            pytest.fail("expected HTTP error for PUT request")
        except urllib.error.HTTPError as error:
            assert error.code == 405
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_list_endpoint_rejects_delete(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_outbox_container(tmp_path, host_port)
    try:
        import urllib.error
        import urllib.request

        request = urllib.request.Request(_list_url(host_port), method="DELETE")
        try:
            urllib.request.urlopen(request, timeout=10)
            pytest.fail("expected HTTP error for DELETE request")
        except urllib.error.HTTPError as error:
            assert error.code == 405
    finally:
        builder.stop(remove=True)


# ---------------------------------------------------------------------------
# Download endpoint happy-path tests
# ---------------------------------------------------------------------------


@pytest.mark.manual
def test_download_endpoint_returns_file_content(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_outbox_container(tmp_path, host_port)
    try:
        outbox_dir = tmp_path / "mount" / "outbox"
        outbox_dir.mkdir(parents=True, exist_ok=True)
        content = b"Hello, outbox download!"
        (outbox_dir / "test.txt").write_bytes(content)

        response = _http_get(_download_url(host_port, "test.txt"))

        assert response.status == 200
        assert response.body == content
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_download_endpoint_deletes_file_after_successful_download(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_outbox_container(tmp_path, host_port)
    try:
        outbox_dir = tmp_path / "mount" / "outbox"
        outbox_dir.mkdir(parents=True, exist_ok=True)
        (outbox_dir / "to_delete.txt").write_text("delete me")

        response = _http_get(_download_url(host_port, "to_delete.txt"))

        assert response.status == 200
        assert not (outbox_dir / "to_delete.txt").exists()
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_download_endpoint_handles_binary_file(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_outbox_container(tmp_path, host_port)
    try:
        outbox_dir = tmp_path / "mount" / "outbox"
        outbox_dir.mkdir(parents=True, exist_ok=True)
        content = bytes(range(256))
        (outbox_dir / "binary.bin").write_bytes(content)

        response = _http_get(_download_url(host_port, "binary.bin"))

        assert response.status == 200
        assert response.body == content
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_download_endpoint_handles_empty_file(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_outbox_container(tmp_path, host_port)
    try:
        outbox_dir = tmp_path / "mount" / "outbox"
        outbox_dir.mkdir(parents=True, exist_ok=True)
        (outbox_dir / "empty.txt").write_text("")

        response = _http_get(_download_url(host_port, "empty.txt"))

        assert response.status == 200
        assert response.body == b""
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_download_endpoint_handles_utf8_filename(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_outbox_container(tmp_path, host_port)
    try:
        outbox_dir = tmp_path / "mount" / "outbox"
        outbox_dir.mkdir(parents=True, exist_ok=True)
        filename = "файл_на_русском.txt"
        content = b"UTF-8 filename download test"
        (outbox_dir / filename).write_bytes(content)

        response = _http_get(_download_url(host_port, filename))

        assert response.status == 200
        assert response.body == content
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_download_endpoint_handles_spaces_in_filename(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_outbox_container(tmp_path, host_port)
    try:
        outbox_dir = tmp_path / "mount" / "outbox"
        outbox_dir.mkdir(parents=True, exist_ok=True)
        filename = "my document.txt"
        content = b"spaces in filename"
        (outbox_dir / filename).write_bytes(content)

        response = _http_get(_download_url(host_port, filename))

        assert response.status == 200
        assert response.body == content
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_download_endpoint_handles_special_chars_in_filename(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_outbox_container(tmp_path, host_port)
    try:
        outbox_dir = tmp_path / "mount" / "outbox"
        outbox_dir.mkdir(parents=True, exist_ok=True)
        filename = "report_(2024)-v2.0_final+.txt"
        content = b"special chars filename test"
        (outbox_dir / filename).write_bytes(content)

        response = _http_get(_download_url(host_port, filename))

        assert response.status == 200
        assert response.body == content
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_download_endpoint_no_size_limit_large_file(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_outbox_container(tmp_path, host_port)
    try:
        outbox_dir = tmp_path / "mount" / "outbox"
        outbox_dir.mkdir(parents=True, exist_ok=True)
        size = 1024 * 1024  # 1 MB
        content = bytes(i % 256 for i in range(size))
        (outbox_dir / "large.bin").write_bytes(content)

        response = _http_get(
            _download_url(host_port, "large.bin"),
            timeout=30,
        )

        assert response.status == 200
        assert len(response.body) == size
        assert response.body == content
    finally:
        builder.stop(remove=True)


# ---------------------------------------------------------------------------
# Download endpoint error handling
# ---------------------------------------------------------------------------


@pytest.mark.manual
def test_download_endpoint_returns_404_for_missing_file(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_outbox_container(tmp_path, host_port)
    try:
        response = _http_get(_download_url(host_port, "nonexistent.txt"))

        assert response.status == 404
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_download_endpoint_rejects_path_traversal(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_outbox_container(tmp_path, host_port)
    try:
        response = _http_get(_download_url(host_port, "../../../etc/passwd"))

        assert response.status == 400
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_download_endpoint_rejects_path_traversal_with_backslashes(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_outbox_container(tmp_path, host_port)
    try:
        response = _http_get(_download_url(host_port, "..\\..\\windows\\system32"))

        assert response.status == 400
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_download_endpoint_rejects_absolute_path(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_outbox_container(tmp_path, host_port)
    try:
        response = _http_get(_download_url(host_port, "/etc/passwd"))

        assert response.status == 400
    finally:
        builder.stop(remove=True)


# ---------------------------------------------------------------------------
# Download endpoint non-GET methods
# ---------------------------------------------------------------------------


@pytest.mark.manual
def test_download_endpoint_rejects_post(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_outbox_container(tmp_path, host_port)
    try:
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
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_download_endpoint_rejects_put(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_outbox_container(tmp_path, host_port)
    try:
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
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_download_endpoint_rejects_delete(tmp_path: Path) -> None:
    host_port = unused_port()
    builder = _run_outbox_container(tmp_path, host_port)
    try:
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
    finally:
        builder.stop(remove=True)


# ---------------------------------------------------------------------------
# Custom endpoint paths
# ---------------------------------------------------------------------------


@pytest.mark.manual
def test_list_endpoint_respects_custom_path(tmp_path: Path) -> None:
    host_port = unused_port()
    custom_list = "/api/custom/outbox-list"
    builder = _run_outbox_container(
        tmp_path,
        host_port,
        list_endpoint_path=custom_list,
    )
    try:
        outbox_dir = tmp_path / "mount" / "outbox"
        outbox_dir.mkdir(parents=True, exist_ok=True)
        (outbox_dir / "test.txt").write_text("custom list path")

        response = _http_get(_list_url(host_port, custom_list))

        assert response.status == 200
        data = response.json()
        assert "test.txt" in data
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_download_endpoint_respects_custom_path(tmp_path: Path) -> None:
    host_port = unused_port()
    custom_download = "/api/custom/outbox-download"
    builder = _run_outbox_container(
        tmp_path,
        host_port,
        download_endpoint_path=custom_download,
    )
    try:
        outbox_dir = tmp_path / "mount" / "outbox"
        outbox_dir.mkdir(parents=True, exist_ok=True)
        content = b"custom download path"
        (outbox_dir / "custom.txt").write_bytes(content)

        response = _http_get(
            _download_url(host_port, "custom.txt", custom_download)
        )

        assert response.status == 200
        assert response.body == content
        assert not (outbox_dir / "custom.txt").exists()
    finally:
        builder.stop(remove=True)
