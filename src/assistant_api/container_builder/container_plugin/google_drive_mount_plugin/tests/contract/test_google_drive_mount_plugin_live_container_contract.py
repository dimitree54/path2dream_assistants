from __future__ import annotations

import json
import os
import textwrap
from collections.abc import Iterator

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin.google_drive_mount_plugin import (
    GoogleDriveMountPluginService,
)
from assistant_api.models import RunningContainer
from google_drive_mount_contract_helpers import unused_port


_DUMMY_CREDENTIALS_JSON = json.dumps(
    {
        "web": {
            "client_id": "client-id",
            "client_secret": "client-secret",
            "redirect_uris": ["http://127.0.0.1/oauth/callback"],
        }
    }
)


@pytest.fixture(scope="module")
def live_google_drive_container() -> Iterator[RunningContainer]:
    previous_credentials = os.environ.get("GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON")
    os.environ["GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON"] = _DUMMY_CREDENTIALS_JSON
    builder = ContainerBuilderService(
        plugins=[
            GoogleDriveMountPluginService(
                host_port=unused_port(),
                drive_folder_name="Notes Assistant API Live Container Contract",
            )
        ],
        container_name=f"notes-assistant-gdrive-live-container-{os.getpid()}",
    )
    try:
        running = builder.build_and_run()
        yield running
    finally:
        try:
            builder.stop(remove=True)
        finally:
            if previous_credentials is None:
                os.environ.pop("GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON", None)
            else:
                os.environ["GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON"] = previous_credentials


@pytest.mark.live_container
def test_live_container_has_real_rclone_fuse_runtime_tools(
    live_google_drive_container: RunningContainer,
) -> None:
    output = _exec_text(
        live_google_drive_container,
        [
            "/bin/sh",
            "-lc",
            textwrap.dedent(
                """
                set -eu
                command -v rclone
                command -v mountpoint
                command -v fusermount3 || command -v fusermount || command -v umount
                rclone version
                rclone unmount --help >/tmp/rclone-unmount-help 2>&1 && exit 13
                grep -q 'unknown command "unmount"' /tmp/rclone-unmount-help
                printf '%s\\n' google-drive-live-runtime-tools-ok
                """
            ),
        ],
        context="Google Drive live runtime tool probe",
    )

    assert "google-drive-live-runtime-tools-ok" in output


@pytest.mark.live_container
def test_live_container_installed_auth_server_unmounts_real_rclone_fuse_mount(
    live_google_drive_container: RunningContainer,
) -> None:
    output = _exec_text(
        live_google_drive_container,
        ["/bin/sh", "-lc", _real_rclone_unmount_probe_script()],
        context="Google Drive live rclone FUSE unmount probe",
    )

    assert "google-drive-live-rclone-unmount-ok" in output


def _real_rclone_unmount_probe_script() -> str:
    auth_server_python = textwrap.dedent(
        f"""
        from pathlib import PurePosixPath
        from google_drive_mount_auth_server import GoogleDriveMountAuthServer

        server = GoogleDriveMountAuthServer(
            auth_port=1,
            host_port=1,
            drive_folder_name="live-container-contract",
            container_path=PurePosixPath("/workspace"),
            remote_name="gdrive",
            mode="rw",
            oauth_authorize_url="http://127.0.0.1/oauth/authorize",
            oauth_token_url="http://127.0.0.1/oauth/token",
            drive_api_base_url="http://127.0.0.1/drive/v3",
            credentials_json={_DUMMY_CREDENTIALS_JSON!r},
        )
        server._unmount_existing_mountpoint()
        """
    )
    return "\n".join(
        [
            "set -eu",
            'test -z "$(find /workspace -mindepth 1 -maxdepth 1 -print -quit)"',
            "mkdir -p /tmp/rclone-local-source",
            "printf '%s' visible-through-real-rclone > /tmp/rclone-local-source/remote-visible-note.txt",
            "rclone mount /tmp/rclone-local-source /workspace --daemon",
            "for _attempt in $(seq 1 50); do",
            "  if mountpoint -q /workspace; then",
            "    break",
            "  fi",
            "  sleep 0.2",
            "done",
            "mountpoint -q /workspace",
            'test "$(cat /workspace/remote-visible-note.txt)" = visible-through-real-rclone',
            "cd /opt/notes-assistant-api/google_drive_mount_plugin",
            "python3 - <<'PY'",
            auth_server_python,
            "PY",
            "if mountpoint -q /workspace; then",
            '  echo "/workspace is still mounted after auth server unmount helper" >&2',
            "  exit 1",
            "fi",
            'test -z "$(find /workspace -mindepth 1 -maxdepth 1 -print -quit)"',
            "rclone unmount /workspace >/tmp/rclone-unmount-after-helper 2>&1 && exit 13",
            "grep -q 'unknown command \"unmount\"' /tmp/rclone-unmount-after-helper",
            "printf '%s\\n' google-drive-live-rclone-unmount-ok",
        ]
    )


def _exec_text(
    running: RunningContainer,
    command: list[str],
    *,
    context: str,
) -> str:
    result = running.container.exec_run(command)
    raw_output = result.output
    output = (
        raw_output.decode("utf-8", errors="replace")
        if isinstance(raw_output, bytes)
        else str(raw_output)
    )
    assert result.exit_code == 0, f"{context} failed with exit code {result.exit_code}: {output}"
    return output
