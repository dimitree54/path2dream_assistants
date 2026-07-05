from __future__ import annotations

import os
from uuid import uuid4

import pytest

from assistant_api.container_builder import (
    ContainerBuilderService,
    RunningContainerCommandRunnerService,
)
from assistant_api.container_builder.container_plugin.opencode_web_server_plugin import (
    OpenCodeWebServerPluginService,
)
from assistant_api.models import (
    ContainerRuntimeContext,
    ContainerSpec,
    ImageSpec,
)


@pytest.mark.live_container
def test_live_container_post_start_health_probe_uses_bounded_wget() -> None:
    suffix = f"{os.getpid()}-{uuid4().hex[:8]}"
    image_tag = f"notes-assistant-opencode-web-wget-{suffix}:test"
    plugin = OpenCodeWebServerPluginService()
    build_builder = ContainerBuilderService(
        plugins=[_PythonProbePlugin(), plugin],
        container_name=f"notes-assistant-opencode-web-build-{suffix}",
        image_tag=image_tag,
    )
    run_builder = ContainerBuilderService(
        plugins=[],
        container_name=f"notes-assistant-opencode-web-wget-{suffix}",
        image_tag=image_tag,
        build_policy="never",
    )

    try:
        _image_spec, container_spec = build_builder._prepare_specs()
        command_text = _post_start_health_command(plugin, container_spec)
        assert "wget -q -T 5 -O -" in command_text
        assert "/global/health" in command_text
        assert " | grep" not in command_text

        build_builder.build()
        running = run_builder.build_and_run()
        try:
            result = RunningContainerCommandRunnerService(running).run_command(
                ["/bin/sh", "-lc", _bounded_wget_probe_script()],
                timeout_seconds=15,
            )
        finally:
            run_builder.stop(remove=True)
    finally:
        _stop_builder_if_started(run_builder)
        _remove_image_if_present(image_tag)

    assert result.exit_code == 0, result.output
    assert "opencode-web-bounded-wget-probe" in result.output


def _post_start_health_command(
    plugin: OpenCodeWebServerPluginService,
    container_spec: ContainerSpec,
) -> str:
    container = _SuccessfulExecContainer()
    plugin.post_start(
        ContainerRuntimeContext(
            docker_client=object(),
            container=container,
            state=container_spec.state,
        )
    )
    return container.commands[0][2]


def _bounded_wget_probe_script() -> str:
    return "\n".join(
        [
            "set -eu",
            "rm -f /tmp/opencode-web-stalled-ready",
            "python3 - <<'PY' &",
            "import socket",
            "import time",
            "from pathlib import Path",
            "server = socket.socket()",
            "server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)",
            "server.bind(('127.0.0.1', 18181))",
            "server.listen(1)",
            "Path('/tmp/opencode-web-stalled-ready').write_text('ready')",
            "connection, _address = server.accept()",
            "time.sleep(30)",
            "connection.close()",
            "server.close()",
            "PY",
            "server_pid=$!",
            (
                "cleanup() { kill \"$server_pid\" 2>/dev/null || true; "
                "wait \"$server_pid\" 2>/dev/null || true; }"
            ),
            "trap cleanup EXIT",
            "while [ ! -f /tmp/opencode-web-stalled-ready ]; do sleep 0.05; done",
            "start=$(python3 - <<'PY'",
            "import time",
            "print(time.monotonic())",
            "PY",
            ")",
            "set +e",
            (
                "wget -q -T 5 -O - http://127.0.0.1:18181/global/health "
                ">/tmp/opencode-web-wget.out 2>/tmp/opencode-web-wget.err"
            ),
            "status=$?",
            "set -e",
            "elapsed=$(python3 - \"$start\" <<'PY'",
            "import sys",
            "import time",
            "print(time.monotonic() - float(sys.argv[1]))",
            "PY",
            ")",
            "test \"$status\" -ne 0",
            "python3 - \"$elapsed\" <<'PY'",
            "import sys",
            "elapsed = float(sys.argv[1])",
            "if elapsed >= 8:",
            "    raise SystemExit(f'bounded wget took too long: {elapsed:.3f}s')",
            "PY",
            "printf '%s\\n' opencode-web-bounded-wget-probe",
        ]
    )


class _PythonProbePlugin:
    name = "opencode-web-python-probe"

    def configure_image(self, image: ImageSpec) -> None:
        image.apk_packages.append("python3")

    def configure_container(self, container: ContainerSpec) -> None:
        return None

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        return None


class _SuccessfulExecContainer:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def exec_run(self, command: list[str]) -> object:
        self.commands.append(command)

        class Result:
            pass

        result = Result()
        result.exit_code = 0
        result.output = b""
        return result


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
