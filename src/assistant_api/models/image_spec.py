from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath


@dataclass(slots=True)
class ImageSpec:
    base_image: str = "ghcr.io/anomalyco/opencode"
    env: dict[str, str] = field(default_factory=dict)
    run_commands: list[str] = field(default_factory=list)
    workdir: PurePosixPath | None = None
    command: list[str] | None = None
