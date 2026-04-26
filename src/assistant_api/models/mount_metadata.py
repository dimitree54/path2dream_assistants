from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal


MountSourceType = Literal["local", "remote"]


@dataclass(slots=True)
class MountMetadata:
    host_path: Path | None
    host_basename: str
    source_key: str
    container_path: PurePosixPath
    mode: str = "rw"
    source_type: MountSourceType = "local"
    remote_name: str | None = None
    remote_folder_id: str | None = None
