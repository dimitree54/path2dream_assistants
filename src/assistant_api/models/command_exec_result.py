from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class CommandExecResult:
    exit_code: int
    output: str
