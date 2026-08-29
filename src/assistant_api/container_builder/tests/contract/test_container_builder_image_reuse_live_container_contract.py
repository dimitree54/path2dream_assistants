from __future__ import annotations

import os
from multiprocessing import get_context
from pathlib import Path
from queue import Empty
from typing import Any

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin.local_dir_mount_plugin import (
    LocalDirMountPluginService,
)


def _build_shared_image(start: Any, results: Any, image_tag: str, name: str) -> None:
    builder = ContainerBuilderService(plugins=[], container_name=name, image_tag=image_tag)
    try:
        start.wait(timeout=10)
        running = builder.build_and_run()
        results.put((name, running.container.id, None))
    except Exception as error:
        results.put((name, None, repr(error)))
    finally:
        _stop_builder_if_started(builder)


@pytest.mark.live_container
def test_live_container_serializes_concurrent_builds_for_shared_image_tag() -> None:
    suffix = str(os.getpid())
    image_tag = f"notes-assistant-concurrent-test-{suffix}:latest"
    container_names = [
        f"notes-assistant-concurrent-first-{suffix}",
        f"notes-assistant-concurrent-second-{suffix}",
    ]
    context = get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_build_shared_image,
            args=(start, results, image_tag, name),
        )
        for name in container_names
    ]

    try:
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            process.join(timeout=180)

        assert all(not process.is_alive() for process in processes)
        outcomes = [results.get(timeout=5) for _process in processes]
        assert {outcome[0] for outcome in outcomes} == set(container_names)
        assert all(
            isinstance(outcome[1], str) and outcome[1] and outcome[2] is None
            for outcome in outcomes
        ), outcomes
    except Empty as error:
        raise AssertionError("concurrent builders did not report both results") from error
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=10)
        for name in container_names:
            _remove_container_if_present(name)
        _remove_image_if_present(image_tag)


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


def _remove_container_if_present(container_name: str) -> None:
    import docker

    client = docker.from_env()
    try:
        client.containers.get(container_name).remove(force=True)
    except Exception:
        return None
