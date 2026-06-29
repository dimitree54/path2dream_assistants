from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

import pytest

from assistant_api.container_builder import (
    ContainerBuilderService,
    ContainerCommandTimeoutError,
    RunningContainerCommandRunnerService,
)
from assistant_api.container_builder.container_plugin.local_dir_mount_plugin import (
    LocalDirMountPluginService,
)


@pytest.fixture(scope="module")
def live_command_runner(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[RunningContainerCommandRunnerService, Path, str]]:
    image_tag = f"notes-assistant-command-runner-test-{os.getpid()}:latest"
    workspace = tmp_path_factory.mktemp("command-runner-workspace")
    builder = ContainerBuilderService(
        plugins=[LocalDirMountPluginService(workspace)],
        container_name=f"notes-assistant-command-runner-{os.getpid()}",
        image_tag=image_tag,
    )

    try:
        running = builder.build_and_run()
        yield RunningContainerCommandRunnerService(running), workspace, image_tag
    finally:
        _stop_builder_if_started(builder)
        _remove_image_if_present(image_tag)


@pytest.mark.live_container
def test_live_container_command_writes_file_to_mounted_workspace(
    live_command_runner: tuple[RunningContainerCommandRunnerService, Path, str],
) -> None:
    runner, workspace, _image_tag = live_command_runner

    result = runner.run_command(
        [
            "/bin/sh",
            "-lc",
            "printf '%s' command-runner-created > produced-by-command.txt",
        ],
        working_dir=PurePosixPath("/workspace"),
        timeout_seconds=10,
    )

    assert result.exit_code == 0
    assert (workspace / "produced-by-command.txt").read_text(
        encoding="utf-8"
    ) == "command-runner-created"


@pytest.mark.live_container
def test_live_container_nonzero_command_returns_exit_code_and_output(
    live_command_runner: tuple[RunningContainerCommandRunnerService, Path, str],
) -> None:
    runner, _workspace, _image_tag = live_command_runner

    result = runner.run_command(
        ["/bin/sh", "-lc", "printf '%s\\n' nonzero-output >&2; exit 23"],
        timeout_seconds=10,
    )

    assert result.exit_code == 23
    assert "nonzero-output" in result.output


@pytest.mark.live_container
def test_live_container_timeout_kills_command_and_container_stays_usable(
    live_command_runner: tuple[RunningContainerCommandRunnerService, Path, str],
) -> None:
    runner, workspace, _image_tag = live_command_runner

    marker = f"issue10-timeout-{os.getpid()}"
    with pytest.raises(ContainerCommandTimeoutError) as error:
        runner.run_command(
            [
                "/bin/sh",
                "-lc",
                (
                    f"trap '' TERM; printf '%s\\n' {marker}; "
                    "while true; do sleep 1; done"
                ),
            ],
            working_dir=PurePosixPath("/workspace"),
            timeout_seconds=1,
        )

    assert marker in error.value.output_tail

    result = runner.run_command(
        [
            "/bin/sh",
            "-lc",
            "printf '%s' usable-after-timeout > after-timeout.txt",
        ],
        working_dir=PurePosixPath("/workspace"),
        timeout_seconds=10,
    )

    assert result.exit_code == 0
    assert (workspace / "after-timeout.txt").read_text(
        encoding="utf-8"
    ) == "usable-after-timeout"


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
