from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ContainerStartupTask:
    name: str
    command: list[str]
    owner_plugin_name: str | None = None
