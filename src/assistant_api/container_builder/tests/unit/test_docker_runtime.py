from __future__ import annotations

from pathlib import PurePosixPath

from typing import Any

from assistant_api.container_builder._docker_runtime import (
    DEFAULT_COMMAND,
    container_command,
    docker_ports,
    docker_volumes,
    run_container,
)
from assistant_api.container_builder._dockerfile import render_dockerfile
from assistant_api.models import (
    ContainerManagedProcess,
    ContainerSpec,
    ContainerStartupTask,
    ImageSpec,
    VolumeMount,
)


def test_default_command_keeps_minimal_container_running() -> None:
    assert DEFAULT_COMMAND == ["sleep", "infinity"]


def test_docker_helpers_render_expected_shapes() -> None:
    assert docker_ports({4096: 4097}) == {"4096/tcp": 4097}
    assert docker_volumes(
        {
            "/tmp/project": VolumeMount(
                source="/tmp/project",
                target=PurePosixPath("/workspace/project"),
            )
        }
    ) == {
        "/tmp/project": {
            "bind": "/workspace/project",
            "mode": "rw",
        }
    }


def test_render_dockerfile_clears_base_entrypoint() -> None:
    assert render_dockerfile(ImageSpec(run_commands=["mkdir -p /workspace"])) == (
        "FROM ghcr.io/anomalyco/opencode\n"
        "ENTRYPOINT []\n"
        "RUN mkdir -p /workspace\n"
    )


def test_render_dockerfile_consolidates_image_dependencies_before_runtime_commands() -> None:
    dockerfile = render_dockerfile(
        ImageSpec(
            apk_packages=["python3", "git", "python3"],
            python_packages=["fastapi", "uvicorn", "fastapi"],
            run_commands=["python3 -c 'print(1)'"],
        )
    )

    assert dockerfile == (
        "FROM ghcr.io/anomalyco/opencode\n"
        "ENTRYPOINT []\n"
        "RUN apk add --no-cache python3 git\n"
        "RUN python3 -m pip install --break-system-packages fastapi uvicorn\n"
        "RUN python3 -c 'print(1)'\n"
    )


def test_run_container_passes_runtime_capabilities_to_docker_sdk() -> None:
    calls = []

    class _Containers:
        def run(self, *_args: Any, **kwargs: Any) -> object:
            calls.append(kwargs)
            return object()

    class _DockerClient:
        containers = _Containers()

    run_container(
        _DockerClient(),
        ContainerSpec(
            name="container-name",
            image_tag="image-tag",
            devices=["/dev/fuse"],
            cap_add=["SYS_ADMIN"],
            security_opt=["apparmor:unconfined"],
        ),
    )

    assert calls[0]["devices"] == ["/dev/fuse"]
    assert calls[0]["cap_add"] == ["SYS_ADMIN"]
    assert calls[0]["security_opt"] == ["apparmor:unconfined"]


def test_container_command_composes_raw_command_with_managed_processes() -> None:
    command = container_command(
        ContainerSpec(
            name="container-name",
            image_tag="image-tag",
            command=["opencode", "web"],
            managed_processes=[
                ContainerManagedProcess(
                    name="auth-server",
                    command=["python3", "/opt/auth.py"],
                )
            ],
        )
    )

    assert command[:2] == ["/bin/sh", "-lc"]
    assert "opencode web &" in command[2]
    assert "python3 /opt/auth.py &" in command[2]
    assert "wait -n" in command[2]


def test_container_command_records_startup_task_status_and_logs() -> None:
    command = container_command(
        ContainerSpec(
            name="container-name",
            image_tag="image-tag",
            startup_tasks=[
                ContainerStartupTask(
                    name="install artifacts",
                    command=["/bin/sh", "-lc", "echo ok"],
                    owner_plugin_name="skills-sync",
                )
            ],
            command=["sleep", "infinity"],
        )
    )

    assert command[:2] == ["/bin/sh", "-lc"]
    shell = command[2]
    assert "status=running" in shell
    assert "status=succeeded" in shell
    assert "status=failed" in shell
    assert "owner=skills-sync" in shell
    assert "name=install artifacts" in shell
    assert "Startup task failed: plugin=skills-sync task=install artifacts" in shell
    assert "exec sleep infinity" in shell
