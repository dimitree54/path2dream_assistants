from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .volume_mount import VolumeMount


@dataclass(slots=True)
class ContainerSpec:
    name: str
    image_tag: str
    env: dict[str, str] = field(default_factory=dict)
    volumes: dict[str, VolumeMount] = field(default_factory=dict)
    ports: dict[int, int] = field(default_factory=dict)
    working_dir: PurePosixPath | None = None
    command: list[str] | None = None
    state: dict[str, object] = field(default_factory=dict)
