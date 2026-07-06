from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True, slots=True)
class LocalSkillPostInstallCommand:
    name: str
    working_dir: PurePosixPath
    command: list[str]
