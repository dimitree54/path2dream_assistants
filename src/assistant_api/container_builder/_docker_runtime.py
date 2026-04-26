from __future__ import annotations

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
        command=container_spec.command or DEFAULT_COMMAND,
        name=container_spec.name,
        detach=True,
        environment=container_spec.env,
        ports=docker_ports(container_spec.ports),
        volumes=docker_volumes(container_spec.volumes),
        working_dir=str(container_spec.working_dir) if container_spec.working_dir else None,
        init=True,
    )


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
