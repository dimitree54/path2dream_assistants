from __future__ import annotations

import os
import shlex
import textwrap
from typing import Iterator

import pytest

from google_drive_mount_contract_helpers import require_manual_env
from google_drive_mount_manual_helpers import (
    MOUNT_PATH,
    LiveGoogleDriveMountRuntime,
    assert_local_port_available,
    host_port_from_google_credentials,
    unique_relative_dir,
    wait_for_remote_file_content,
)


@pytest.fixture(scope="session")
def live_runtime() -> Iterator[LiveGoogleDriveMountRuntime]:
    require_manual_env()
    raw_credentials = os.environ["GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON"]
    host_port = host_port_from_google_credentials(raw_credentials)
    assert_local_port_available(host_port)
    runtime = LiveGoogleDriveMountRuntime(
        host_port=host_port
    )
    runtime.start()
    runtime.ensure_mounted()
    try:
        yield runtime
    finally:
        try:
            runtime.logout()
        finally:
            runtime.stop(remove=True)


@pytest.mark.manual
def test_manual_live_mount_exposes_expected_preexisting_root_file(
    live_runtime: LiveGoogleDriveMountRuntime,
) -> None:
    live_runtime.wait_for_mounted_status(timeout_seconds=300)
    live_runtime.exec_text(
        ["mountpoint", "-q", str(MOUNT_PATH)],
        context="mountpoint verification",
    )
    file_body = live_runtime.exec_text(
        ["/bin/sh", "-lc", "cat /workspace/project/test.md"],
        context="read pre-existing Drive root file",
    )
    assert file_body == "zebra"


@pytest.mark.manual
def test_manual_live_mount_supports_regular_filesystem_operations(
    live_runtime: LiveGoogleDriveMountRuntime,
) -> None:
    live_runtime.wait_for_mounted_status(timeout_seconds=300)
    relative_dir = unique_relative_dir("filesystem")
    mount_dir = MOUNT_PATH / relative_dir
    try:
        script = textwrap.dedent(
            f"""
            from pathlib import Path

            base = Path({str(mount_dir)!r})
            base.mkdir(parents=True, exist_ok=False)

            text_path = base / "alpha.txt"
            text_path.write_bytes(b"0123456789")
            with text_path.open("r+b") as handle:
                handle.seek(4)
                handle.write(b"AB")
            with text_path.open("ab") as handle:
                handle.write(b"END")
            with text_path.open("r+b") as handle:
                handle.truncate(11)
            assert text_path.read_bytes() == b"0123AB6789E"

            renamed_path = base / "renamed.txt"
            text_path.rename(renamed_path)
            assert renamed_path.read_bytes() == b"0123AB6789E"

            nested = base / "nested"
            nested.mkdir()
            nested_file = nested / "deep.txt"
            nested_file.write_text("needle", encoding="utf-8")

            binary_path = base / "bytes.bin"
            binary_path.write_bytes(bytes(range(32)))
            assert binary_path.read_bytes() == bytes(range(32))

            special_name = base / "report_(2026)-v1+final.txt"
            special_name.write_text("ascii name works", encoding="utf-8")
            """
        )
        live_runtime.exec_text(
            ["python3", "-c", script],
            context="filesystem behavior contract",
        )

        list_output = live_runtime.exec_text(
            ["/bin/sh", "-lc", f"ls -1 {shlex.quote(str(mount_dir))}"],
            context="list directory entries",
        ).splitlines()
        assert "renamed.txt" in list_output
        assert "nested" in list_output
        assert "bytes.bin" in list_output
        assert "report_(2026)-v1+final.txt" in list_output

        find_output = live_runtime.exec_text(
            [
                "/bin/sh",
                "-lc",
                f"find {shlex.quote(str(mount_dir))} -type f | sort",
            ],
            context="find files recursively",
        )
        assert str(mount_dir / "renamed.txt") in find_output
        assert str(mount_dir / "nested" / "deep.txt") in find_output
        assert str(mount_dir / "bytes.bin") in find_output
    finally:
        live_runtime.exec_text(
            ["/bin/sh", "-lc", f"rm -rf {shlex.quote(str(mount_dir))}"],
            context="cleanup filesystem test directory",
        )
        live_runtime.exec_text(
            ["/bin/sh", "-lc", f"test ! -e {shlex.quote(str(mount_dir))}"],
            context="verify cleanup for filesystem test directory",
        )


@pytest.mark.manual
def test_manual_live_mount_syncs_local_write_to_drive_remote(
    live_runtime: LiveGoogleDriveMountRuntime,
) -> None:
    live_runtime.wait_for_mounted_status(timeout_seconds=300)
    relative_dir = unique_relative_dir("sync")
    relative_file = f"{relative_dir}/sync.txt"
    mount_file = MOUNT_PATH / relative_file
    expected = f"sync-marker-{os.getpid()}"
    try:
        script = textwrap.dedent(
            f"""
            from pathlib import Path

            target = Path({str(mount_file)!r})
            target.parent.mkdir(parents=True, exist_ok=False)
            target.write_text({expected!r}, encoding="utf-8")
            """
        )
        live_runtime.exec_text(
            ["python3", "-c", script],
            context="write sync marker locally through mount",
        )

        wait_for_remote_file_content(
            live_runtime,
            relative_path=relative_file,
            expected_content=expected,
            timeout_seconds=240,
        )

        remote_listing = live_runtime.exec_text(
            ["rclone", "lsf", f"gdrive:{relative_dir}"],
            context="list remote directory",
        )
        assert "sync.txt" in remote_listing.splitlines()
    finally:
        mount_dir = MOUNT_PATH / relative_dir
        live_runtime.exec_text(
            ["/bin/sh", "-lc", f"rm -rf {shlex.quote(str(mount_dir))}"],
            context="cleanup sync test directory",
        )
        live_runtime.exec_text(
            ["/bin/sh", "-lc", f"test ! -e {shlex.quote(str(mount_dir))}"],
            context="verify cleanup for sync test directory",
        )


@pytest.mark.manual
def test_manual_live_mount_restores_from_persisted_auth_without_second_login(
    live_runtime: LiveGoogleDriveMountRuntime,
) -> None:
    live_runtime.ensure_mounted(timeout_seconds=300)
    live_runtime.restart_container()
    status = live_runtime.wait_for_mounted_without_login(timeout_seconds=180)
    assert status["authValid"] is True
    assert status["mounted"] is True
    assert status["state"] == "mounted"
    live_runtime.exec_text(
        ["mountpoint", "-q", str(MOUNT_PATH)],
        context="mountpoint verification after restart restore",
    )
