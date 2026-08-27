from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

MAX_LINUX_ID = 2**32 - 2


@dataclass(frozen=True, slots=True)
class ContainerExecutionIdentity:
    uid: int
    gid: int
    umask: int

    def __post_init__(self) -> None:
        if not _valid_linux_id(self.uid):
            raise ValueError("uid must be a positive non-root integer")
        if not _valid_linux_id(self.gid):
            raise ValueError("gid must be a positive non-root integer")
        if (
            not isinstance(self.umask, int)
            or isinstance(self.umask, bool)
            or self.umask < 0
            or self.umask > 0o777
        ):
            raise ValueError("umask must be an integer between 0o000 and 0o777")

    @property
    def docker_user(self) -> str:
        return f"{self.uid}:{self.gid}"

    @property
    def umask_text(self) -> str:
        return f"{self.umask:04o}"

    def wrap_command(self, command: list[str]) -> list[str]:
        return [
            "/bin/sh",
            "-c",
            'umask "$1"; shift; exec "$@"',
            "notes-assistant-execution-identity",
            self.umask_text,
            *command,
        ]

    def writable_directory_error(self, path: Path) -> str | None:
        try:
            info = path.stat()
        except FileNotFoundError:
            return "directory does not exist"
        if not path.is_dir():
            return "path is not a directory"
        if info.st_uid != self.uid or info.st_gid != self.gid:
            return (
                f"ownership is {info.st_uid}:{info.st_gid}, "
                f"expected {self.docker_user}"
            )
        if not info.st_mode & stat.S_IWUSR:
            return "directory is not writable by its owner"
        if not os.access(path, os.W_OK):
            return "directory is not writable by the caller"
        return None


def _valid_linux_id(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 < value <= MAX_LINUX_ID
    )
