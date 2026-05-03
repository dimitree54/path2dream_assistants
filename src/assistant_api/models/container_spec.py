from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .container_managed_process import ContainerManagedProcess
from .container_startup_task import ContainerStartupTask
from .published_port import PublishedPort
from .volume_mount import VolumeMount


@dataclass(slots=True)
class ContainerSpec:
    name: str
    image_tag: str
    env: dict[str, str] = field(default_factory=dict)
    volumes: dict[str, VolumeMount] = field(default_factory=dict)
    ports: dict[int, int | PublishedPort] = field(default_factory=dict)
    working_dir: PurePosixPath | None = None
    command: list[str] | None = None
    devices: list[str] = field(default_factory=list)
    cap_add: list[str] = field(default_factory=list)
    security_opt: list[str] = field(default_factory=list)
    startup_tasks: list[ContainerStartupTask] = field(default_factory=list)
    managed_processes: list[ContainerManagedProcess] = field(default_factory=list)
    state: dict[str, object] = field(default_factory=dict)
