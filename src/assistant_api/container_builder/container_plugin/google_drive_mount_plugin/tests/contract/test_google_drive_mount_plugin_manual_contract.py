from __future__ import annotations

import os
import time
from pathlib import PurePosixPath

import pytest

from assistant_api.container_builder import ContainerBuilderService
from google_drive_mount_contract_helpers import (
    auth_port,
    extract_login_href,
    read_url,
    require_manual_env,
    service_class,
    service_url,
    status_json,
)


@pytest.mark.manual
def test_manual_live_google_drive_mount_round_trip() -> None:
    require_manual_env()
    host_port = auth_port()
    plugin = service_class()(
        host_port=host_port,
        drive_folder_name="Notes Assistant API Manual Folder",
        container_path=PurePosixPath("/workspace/project"),
    )
    builder = ContainerBuilderService(
        plugins=[plugin],
        container_name=f"notes-assistant-gdrive-manual-{os.getpid()}",
    )
    running = builder.build_and_run()
    try:
        login = read_url(service_url(host_port, "/login"))
        assert login.status == 200
        authorize_url = extract_login_href(login.text)
        print(f"\nOpen this Google OAuth URL to authorize the manual test:\n{authorize_url}\n")
        deadline = time.monotonic() + 180
        status = status_json(host_port)
        while status["state"] != "mounted" and time.monotonic() < deadline:
            time.sleep(2)
            status = status_json(host_port)
        assert status["state"] == "mounted"
        assert status["mounted"] is True
        result = running.container.exec_run(
            [
                "/bin/sh",
                "-lc",
                (
                    "mountpoint -q /workspace/project "
                    "&& printf live-google-drive-mount > "
                    "/workspace/project/.notes-assistant-manual-mount-test "
                    "&& test -f /workspace/project/.notes-assistant-manual-mount-test"
                ),
            ]
        )
        assert result.exit_code == 0, result.output
    finally:
        read_url(service_url(host_port, "/logout"))
        builder.stop(remove=True)
