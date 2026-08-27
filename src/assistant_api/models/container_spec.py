from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .container_managed_process import ContainerManagedProcess
from .container_execution_identity import ContainerExecutionIdentity
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
    mem_limit: str | None = None
    shm_size: str | None = None
    restart_policy: str | None = None
    execution_identity: ContainerExecutionIdentity | None = None
    startup_tasks: list[ContainerStartupTask] = field(default_factory=list)
    managed_processes: list[ContainerManagedProcess] = field(default_factory=list)
    state: dict[str, object] = field(default_factory=dict)
    _execution_identity_requirements: list[ContainerExecutionIdentity] = field(
        default_factory=list,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        if self.execution_identity is not None:
            self._execution_identity_requirements.append(self.execution_identity)

    def require_execution_identity(self, identity: ContainerExecutionIdentity) -> None:
        self._execution_identity_requirements.append(identity)
        if self.execution_identity is None:
            self.execution_identity = identity

    def execution_identity_requirements(self) -> set[ContainerExecutionIdentity]:
        return set(self._execution_identity_requirements)
