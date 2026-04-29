from __future__ import annotations

import socket
from pathlib import PurePosixPath
from typing import Any

from assistant_api.models import MountMetadata


def service_class() -> type[Any]:
    from assistant_api.container_builder.container_plugin.inbox_upload_plugin import (
        InboxUploadPluginService,
    )

    return InboxUploadPluginService


def unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def mount_metadata(
    *,
    container_path: PurePosixPath = PurePosixPath("/workspace/project"),
    host_basename: str = "project",
    source_key: str = "mount-source",
    source_type: Any = "local",
    host_path: Any = None,
    mode: str = "rw",
    remote_name: str | None = None,
    remote_folder_id: str | None = None,
) -> MountMetadata:
    return MountMetadata(
        host_path=host_path,
        host_basename=host_basename,
        source_key=source_key,
        container_path=container_path,
        mode=mode,
        source_type=source_type,
        remote_name=remote_name,
        remote_folder_id=remote_folder_id,
    )


class FakeContainer:
    def __init__(self, exit_code: int = 0, output: str = "") -> None:
        self.exit_code = exit_code
        self.output = output
        self.commands: list[list[str]] = []

    def exec_run(self, command: list[str]) -> object:
        self.commands.append(command)
        exit_code = self.exit_code
        output = self.output.encode("utf-8")

        class Result:
            pass

        result = Result()
        result.exit_code = exit_code
        result.output = output
        return result
