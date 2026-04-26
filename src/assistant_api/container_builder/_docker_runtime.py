from __future__ import annotations

import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assistant_api.models import ContainerSpec, ImageSpec, VolumeMount

from ._dockerfile import render_dockerfile


DEFAULT_COMMAND = ["sleep", "infinity"]


@dataclass(slots=True)
class BuiltImage:
    tag: str
    image: Any
    image_spec: ImageSpec


def build_image(docker_client: Any, image_spec: ImageSpec, image_tag: str) -> BuiltImage:
    with tempfile.TemporaryDirectory(prefix="container-builder-") as context_dir:
        dockerfile_path = Path(context_dir) / "Dockerfile"
        dockerfile_path.write_text(render_dockerfile(image_spec), encoding="utf-8")
        image, _logs = docker_client.images.build(
            path=context_dir,
            dockerfile="Dockerfile",
            tag=image_tag,
            rm=True,
            forcerm=True,
        )
    return BuiltImage(tag=image_tag, image=image, image_spec=image_spec)


def run_container(docker_client: Any, container_spec: ContainerSpec) -> Any:
    return docker_client.containers.run(
        container_spec.image_tag,
        command=container_command(container_spec),
        name=container_spec.name,
        detach=True,
        environment=container_spec.env,
        ports=docker_ports(container_spec.ports),
        volumes=docker_volumes(container_spec.volumes),
        working_dir=str(container_spec.working_dir) if container_spec.working_dir else None,
        devices=container_spec.devices,
        cap_add=container_spec.cap_add,
        security_opt=container_spec.security_opt,
        init=True,
    )


def container_command(container_spec: ContainerSpec) -> list[str]:
    command = container_spec.command or DEFAULT_COMMAND
    if container_spec.managed_processes:
        long_running_commands = []
        if container_spec.command is not None:
            long_running_commands.append(container_spec.command)
        long_running_commands.extend(process.command for process in container_spec.managed_processes)
        command = ["/bin/sh", "-lc", _managed_process_command(long_running_commands)]
    if not container_spec.startup_tasks:
        return command

    shell_parts = [shlex.join(task.command) for task in container_spec.startup_tasks]
    shell_parts.append("exec " + shlex.join(command))
    return ["/bin/sh", "-lc", " && ".join(shell_parts)]


def _managed_process_command(commands: list[list[str]]) -> str:
    parts = []
    for command in commands:
        parts.append(shlex.join(command) + " &")
        parts.append('pids="$pids $!"')
    parts.append("wait -n")
    parts.append("status=$?")
    parts.append("kill $pids 2>/dev/null || true")
    parts.append("wait 2>/dev/null || true")
    parts.append("exit $status")
    return "\n".join(parts)


def ensure_named_volumes(docker_client: Any, container_spec: ContainerSpec) -> None:
    for mount in container_spec.volumes.values():
        if mount.type != "volume":
            continue
        try:
            docker_client.volumes.get(mount.source)
        except Exception:
            docker_client.volumes.create(name=mount.source)


def docker_ports(ports: dict[int, int]) -> dict[str, int]:
    return {f"{container_port}/tcp": host_port for container_port, host_port in ports.items()}


def docker_volumes(volumes: dict[str, VolumeMount]) -> dict[str, dict[str, str]]:
    return {
        source: {
            "bind": str(mount.target),
            "mode": mount.mode,
        }
        for source, mount in volumes.items()
    }
