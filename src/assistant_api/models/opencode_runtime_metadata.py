from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(slots=True)
class OpenCodeRuntimeMetadata:
    working_dir: PurePosixPath
    api_container_port: int
