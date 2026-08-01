from __future__ import annotations

import os

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin.container_environment_plugin import (
    ContainerEnvironmentPluginService,
)


@pytest.mark.live_container
def test_configured_environment_is_visible_inside_live_container() -> None:
    builder = ContainerBuilderService(
        plugins=[
            ContainerEnvironmentPluginService(
                environment={
                    "OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS": "600000"
                }
            )
        ],
        container_name=f"notes-assistant-container-environment-test-{os.getpid()}",
    )

    try:
        running = builder.build_and_run()
    except Exception as error:
        _stop_builder_if_started(builder)
        pytest.fail(
            "container environment plugin image must build and start; "
            f"got {type(error).__name__}: {error}"
        )

    try:
        result = running.container.exec_run(
            [
                "/bin/sh",
                "-lc",
                "test \"$OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS\" = 600000 "
                "&& printf '%s\\n' container-environment-live-probe-ok",
            ]
        )
        output = _decode_output(result.output)
        assert result.exit_code == 0, output
        assert "container-environment-live-probe-ok" in output
    finally:
        builder.stop(remove=True)


def _decode_output(output: bytes | str) -> str:
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output


def _stop_builder_if_started(builder: ContainerBuilderService) -> None:
    try:
        builder.stop(remove=True)
    except Exception:
        return None
