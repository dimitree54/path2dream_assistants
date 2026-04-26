from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .container_spec import ContainerSpec


@dataclass(slots=True)
class RunningContainer:
    container: Any
    container_spec: ContainerSpec

    @property
    def id(self) -> str:
        return self.container.id

    @property
    def name(self) -> str:
        return self.container.name
