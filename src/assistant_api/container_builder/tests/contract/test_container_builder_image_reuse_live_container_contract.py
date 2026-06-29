from __future__ import annotations

import os
from pathlib import Path

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin.local_dir_mount_plugin import (
    LocalDirMountPluginService,
)


@pytest.mark.live_container
def test_live_container_reuses_prebuilt_image_with_different_workspace(
    tmp_path: Path,
) -> None:
    image_tag = f"notes-assistant-reuse-test-{os.getpid()}:latest"
    first_workspace = tmp_path / "first-workspace"
    second_workspace = tmp_path / "second-workspace"
    first_workspace.mkdir()
    second_workspace.mkdir()
    (first_workspace / "workspace-id.txt").write_text("first", encoding="utf-8")
    (second_workspace / "workspace-id.txt").write_text("second", encoding="utf-8")

    first_builder = ContainerBuilderService(
        plugins=[LocalDirMountPluginService(first_workspace)],
        container_name=f"notes-assistant-reuse-first-{os.getpid()}",
        image_tag=image_tag,
    )
    second_builder = ContainerBuilderService(
        plugins=[LocalDirMountPluginService(second_workspace)],
        container_name=f"notes-assistant-reuse-second-{os.getpid()}",
        image_tag=image_tag,
        build_policy="never",
    )

    try:
        first_running = first_builder.build_and_run()
        assert _workspace_id(first_running.container) == "first"
        first_builder.stop(remove=True)

        second_running = second_builder.build_and_run()
        assert _workspace_id(second_running.container) == "second"
    finally:
        _stop_builder_if_started(second_builder)
        _stop_builder_if_started(first_builder)
        _remove_image_if_present(image_tag)


def _workspace_id(container: object) -> str:
    result = container.exec_run(["/bin/sh", "-lc", "cat /workspace/workspace-id.txt"])
    output = _decode_output(result.output).strip()
    assert result.exit_code == 0, output
    return output


def _decode_output(output: bytes | str) -> str:
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output


def _stop_builder_if_started(builder: ContainerBuilderService) -> None:
    try:
        builder.stop(remove=True)
    except Exception:
        return None


def _remove_image_if_present(image_tag: str) -> None:
    import docker

    client = docker.from_env()
    try:
        client.images.remove(image=image_tag, force=True)
    except Exception:
        return None
