from __future__ import annotations

import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assistant_api.models import (
    ContainerSpec,
    ContainerStartupTask,
    ImageSpec,
    PublishedPort,
    VolumeMount,
)

from ._dockerfile import render_dockerfile


DEFAULT_COMMAND = ["sleep", "infinity"]
STARTUP_TASK_STATUS_DIR = "/tmp/notes-assistant-startup-tasks"


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


def image_exists(docker_client: Any, image_tag: str) -> bool:
    from docker.errors import ImageNotFound

    try:
        docker_client.images.get(image_tag)
    except ImageNotFound:
        return False
    return True


def run_container(docker_client: Any, container_spec: ContainerSpec) -> Any:
    command = container_command(container_spec)
    user = None
    if container_spec.execution_identity is not None:
        command = container_spec.execution_identity.wrap_command(command)
        user = container_spec.execution_identity.docker_user
    return docker_client.containers.run(
        container_spec.image_tag,
        command=command,
        user=user,
        name=container_spec.name,
        detach=True,
        environment=container_spec.env,
        ports=docker_ports(container_spec.ports),
        volumes=docker_volumes(container_spec.volumes),
        working_dir=str(container_spec.working_dir) if container_spec.working_dir else None,
        devices=container_spec.devices,
        cap_add=container_spec.cap_add,
        security_opt=container_spec.security_opt,
        mem_limit=container_spec.mem_limit,
        shm_size=container_spec.shm_size,
        restart_policy={"Name": container_spec.restart_policy} if container_spec.restart_policy else None,
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

    shell_parts = [
        _startup_task_command(index, task)
        for index, task in enumerate(container_spec.startup_tasks)
    ]
    shell_parts.append("exec " + shlex.join(command))
    return ["/bin/sh", "-lc", "set -eu\n" + "\n".join(shell_parts)]


def startup_task_status_path(index: int, task: ContainerStartupTask) -> str:
    return (
        f"{STARTUP_TASK_STATUS_DIR}/"
        f"{index:03d}-{_safe_task_token(task.owner_plugin_name or 'unknown')}-"
        f"{_safe_task_token(task.name)}.status"
    )


def startup_task_log_path(index: int, task: ContainerStartupTask) -> str:
    return startup_task_status_path(index, task) + ".log"


def _startup_task_command(index: int, task: ContainerStartupTask) -> str:
    marker_path = startup_task_status_path(index, task)
    log_path = startup_task_log_path(index, task)
    owner = task.owner_plugin_name or "unknown"
    return "\n".join(
        [
            f"mkdir -p {shlex.quote(STARTUP_TASK_STATUS_DIR)}",
            _write_startup_status(marker_path, "running", owner, task.name, ""),
            "set +e",
            f"( {shlex.join(task.command)} ) > {shlex.quote(log_path)} 2>&1",
            "task_status=$?",
            "set -e",
            'if [ "$task_status" -eq 0 ]; then',
            "  "
            + _write_startup_status(
                marker_path,
                "succeeded",
                owner,
                task.name,
                "$task_status",
                expand_exit_code=True,
            ),
            "else",
            "  "
            + _write_startup_status(
                marker_path,
                "failed",
                owner,
                task.name,
                "$task_status",
                expand_exit_code=True,
            ),
            "  "
            + (
                "printf '%s\\n' "
                + shlex.quote(
                    f"Startup task failed: plugin={owner} task={task.name}"
                )
                + " >&2"
            ),
            f"  tail -c 4000 {shlex.quote(log_path)} >&2 || true",
            '  exit "$task_status"',
            "fi",
        ]
    )


def _write_startup_status(
    marker_path: str,
    status: str,
    owner: str,
    task_name: str,
    exit_code: str,
    *,
    expand_exit_code: bool = False,
) -> str:
    lines = [
        f"status={status}",
        f"owner={owner}",
        f"name={task_name}",
    ]
    if expand_exit_code:
        arguments = " ".join(shlex.quote(line) for line in lines)
        return (
            "printf '%s\\n' "
            f"{arguments} \"exit_code={exit_code}\" > {shlex.quote(marker_path)}"
        )
    lines.append(f"exit_code={exit_code}")
    arguments = " ".join(shlex.quote(line) for line in lines)
    return f"printf '%s\\n' {arguments} > {shlex.quote(marker_path)}"


def _safe_task_token(value: str) -> str:
    token = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "_"
        for char in value
    )
    return token or "unnamed"


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


def docker_ports(ports: dict[int, int | PublishedPort]) -> dict[str, int | tuple[str, int]]:
    return {
        f"{container_port}/tcp": _docker_port_binding(published_port)
        for container_port, published_port in ports.items()
    }


def _docker_port_binding(published_port: int | PublishedPort) -> int | tuple[str, int]:
    if isinstance(published_port, PublishedPort):
        if published_port.host is None:
            return published_port.host_port
        return (published_port.host, published_port.host_port)
    return published_port


def docker_volumes(volumes: dict[str, VolumeMount]) -> dict[str, dict[str, str]]:
    return {
        source: {
            "bind": str(mount.target),
            "mode": mount.mode,
        }
        for source, mount in volumes.items()
    }
