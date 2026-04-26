from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(slots=True)
class MountMetadata:
    host_path: Path
    host_basename: str
    source_key: str
    container_path: PurePosixPath
    mode: str = "rw"
