from __future__ import annotations

from pathlib import PurePosixPath

from assistant_api.container_builder._docker_runtime import DEFAULT_COMMAND, docker_ports, docker_volumes
from assistant_api.container_builder._dockerfile import render_dockerfile
from assistant_api.models import ImageSpec, VolumeMount


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
