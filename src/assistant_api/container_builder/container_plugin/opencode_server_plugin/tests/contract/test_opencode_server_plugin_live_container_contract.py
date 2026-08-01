from __future__ import annotations

import os
from uuid import uuid4

import pytest

from assistant_api.container_builder import (
    ContainerBuilderService,
    RunningContainerCommandRunnerService,
)


@pytest.mark.live_container
def test_live_container_retry_patch_fails_permanent_errors_and_bounds_transient_retries() -> None:
    suffix = f"{os.getpid()}-{uuid4().hex[:8]}"
    image_tag = f"notes-assistant-opencode-retry-{suffix}:test"
    builder = ContainerBuilderService(
        plugins=[OpenCodeServerPluginService(max_retries=2)],
        container_name=f"notes-assistant-opencode-retry-{suffix}",
        image_tag=image_tag,
    )

    try:
        builder.build()
        version = builder._docker_client.containers.run(
            image_tag,
            ["opencode", "--version"],
            remove=True,
        )
        permanent = builder._docker_client.containers.run(
            image_tag,
            ["/bin/sh", "-lc", _provider_failure_probe(status=404, include_title=True)],
            remove=True,
        )
        transient = builder._docker_client.containers.run(
            image_tag,
            ["/bin/sh", "-lc", _provider_failure_probe(status=500, include_title=False)],
            remove=True,
        )
    finally:
        _remove_image_if_present(image_tag)

    assert version.strip() == b"1.18.10"
    assert b"status=1" in permanent
    assert b"count=2" in permanent
    assert b"status=1" in transient
    assert b"count=3" in transient


def _provider_failure_probe(*, status: int, include_title: bool) -> str:
    title_option = "" if include_title else "--title controlled-transient"
    body = "model_not_found: Model not found" if status == 404 else "overloaded"
    return f"""
set -eu
mkdir -p /tmp/retry-probe
cat >/tmp/retry-probe/opencode.json <<'EOF'
{{"provider":{{"openai":{{"options":{{"baseURL":"http://127.0.0.1:18080/v1","apiKey":"test"}}}}}}}}
EOF
cat >/tmp/retry-server.py <<'PY'
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        count = Path("/tmp/retry-count")
        count.write_text(str(int(count.read_text()) + 1) if count.exists() else "1")
        body = {body!r}.encode()
        self.send_response({status})
        self.send_header("content-type", "text/plain")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *_args):
        pass
HTTPServer(("127.0.0.1", 18080), Handler).serve_forever()
PY
python3 /tmp/retry-server.py &
server_pid=$!
trap 'kill "$server_pid" 2>/dev/null || true' EXIT
cd /tmp/retry-probe
set +e
OPENAI_API_KEY=test timeout 20 opencode run {title_option} --model openai/gpt-4o-mini hello >/tmp/retry-out 2>/tmp/retry-err
status=$?
set -e
printf 'status=%s count=%s\n' "$status" "$(cat /tmp/retry-count)"
test "$status" -ne 124
"""
from assistant_api.container_builder.container_plugin.opencode_server_plugin import (
    OpenCodeServerPluginService,
)
from assistant_api.models import (
    ContainerRuntimeContext,
    ContainerSpec,
    ImageSpec,
)


@pytest.mark.live_container
def test_live_container_post_start_health_probe_uses_bounded_wget() -> None:
    suffix = f"{os.getpid()}-{uuid4().hex[:8]}"
    image_tag = f"notes-assistant-opencode-server-wget-{suffix}:test"
    plugin = OpenCodeServerPluginService()
    build_builder = ContainerBuilderService(
        plugins=[_PythonProbePlugin(), plugin],
        container_name=f"notes-assistant-opencode-server-build-{suffix}",
        image_tag=image_tag,
    )
    run_builder = ContainerBuilderService(
        plugins=[],
        container_name=f"notes-assistant-opencode-server-wget-{suffix}",
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
    assert "opencode-server-bounded-wget-probe" in result.output


def _post_start_health_command(
    plugin: OpenCodeServerPluginService,
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
            "rm -f /tmp/opencode-server-stalled-ready",
            "python3 - <<'PY' &",
            "import socket",
            "import time",
            "from pathlib import Path",
            "server = socket.socket()",
            "server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)",
            "server.bind(('127.0.0.1', 18181))",
            "server.listen(1)",
            "Path('/tmp/opencode-server-stalled-ready').write_text('ready')",
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
            "while [ ! -f /tmp/opencode-server-stalled-ready ]; do sleep 0.05; done",
            "start=$(python3 - <<'PY'",
            "import time",
            "print(time.monotonic())",
            "PY",
            ")",
            "set +e",
            (
                "wget -q -T 5 -O - http://127.0.0.1:18181/global/health "
                ">/tmp/opencode-server-wget.out 2>/tmp/opencode-server-wget.err"
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
            "printf '%s\\n' opencode-server-bounded-wget-probe",
        ]
    )


class _PythonProbePlugin:
    name = "opencode-server-python-probe"

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
