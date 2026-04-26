from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal


VolumeType = Literal["bind", "volume"]


@dataclass(slots=True)
class VolumeMount:
    source: str
    target: PurePosixPath
    mode: str = "rw"
    type: VolumeType = "bind"
