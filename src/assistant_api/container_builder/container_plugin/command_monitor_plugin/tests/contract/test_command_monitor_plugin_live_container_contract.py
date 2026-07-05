from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from uuid import uuid4

import pytest

from assistant_api.container_builder import (
    ContainerBuilderService,
    RunningContainerCommandRunnerService,
)
from assistant_api.container_builder.container_plugin.command_monitor_plugin import (
    CommandMonitorPluginService,
)
from assistant_api.container_builder.container_plugin.local_dir_mount_plugin import (
    LocalDirMountPluginService,
)
from assistant_api.container_builder.container_plugin.openai_provider_api_token_plugin import (
    OpenAIProviderApiTokenPluginService,
)
from assistant_api.container_builder.container_plugin.opencode_persistence_plugin import (
    OpenCodePersistencePluginService,
)
from assistant_api.container_builder.container_plugin.opencode_server_plugin import (
    OpenCodeServerPluginService,
)
from command_monitor_contract_helpers import unused_port


TOKEN_ENV_VAR = "OPENAI_API_KEY"
LIVE_MODEL = "openai/gpt-4.1-mini"
MISSING_BINARY = "notes-assistant-missing-binary-probe"
ANSWER_MARKER = "COMMAND_MONITOR_PROBE_DONE"
PLUGIN_FILE = "/root/.config/opencode/plugins/notes-assistant-command-monitor.js"
LOG_FILE = "/tmp/notes-assistant/command-monitor/failed-commands.jsonl"


@pytest.mark.live_container
def test_live_container_logs_failed_opencode_bash_command(tmp_path: Path) -> None:
    _require_openai_live_account()

    suffix = f"{os.getpid()}-{uuid4().hex[:8]}"
    log_volume = f"command_monitor_logs_{suffix}"
    image_tag = f"notes-assistant-command-monitor-{suffix}:test"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "opencode.json").write_text(
        json.dumps(
            {
                "$schema": "https://opencode.ai/config.json",
                "permission": {"bash": "allow"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    builder = ContainerBuilderService(
        plugins=[
            OpenCodePersistencePluginService(
                config_volume=f"unused_command_monitor_config_{suffix}",
                data_volume=f"unused_command_monitor_data_{suffix}",
                persist_auth=False,
                persist_chat_history=False,
                persist_opencode_artifacts=False,
                persist_skills=False,
                persist_agents=False,
            ),
            LocalDirMountPluginService(workspace),
            CommandMonitorPluginService(log_volume=log_volume),
            OpenCodeServerPluginService(host_port=unused_port()),
            OpenAIProviderApiTokenPluginService(
                api_token_env_var=TOKEN_ENV_VAR,
                opencode_model=LIVE_MODEL,
            ),
        ],
        container_name=f"notes-assistant-command-monitor-{suffix}",
        image_tag=image_tag,
    )

    try:
        try:
            running = builder.build_and_run()
        except Exception as error:
            _stop_builder_if_started(builder)
            pytest.fail(
                "command monitor live container failed before probes; "
                f"got {type(error).__name__}: {error}\n\n{_docker_build_log(error)}"
            )

        try:
            _assert_plugin_installed_and_opencode_healthy(running.container)
            _assert_failed_command_logged(running)
        finally:
            builder.stop(remove=True)
    finally:
        _stop_builder_if_started(builder)
        _remove_image_if_present(image_tag)
        _remove_volume_if_present(log_volume)


def _require_openai_live_account() -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        pytest.skip("OpenAI paid-account live probe is validated locally under Doppler")
    if not os.environ.get(TOKEN_ENV_VAR):
        pytest.skip("OPENAI_API_KEY is required for command monitor live container test")


def _assert_plugin_installed_and_opencode_healthy(container: object) -> None:
    result = container.exec_run(["/bin/sh", "-lc", _installed_probe_script()])
    output = _decode_output(result.output)
    assert result.exit_code == 0, output
    assert "command-monitor-installed" in output


def _assert_failed_command_logged(running_container: object) -> None:
    runner = RunningContainerCommandRunnerService(running_container)
    result = runner.run_command(
        [
            "opencode",
            "run",
            "--dir",
            "/workspace",
            "--model",
            LIVE_MODEL,
            "--format",
            "json",
            (
                "Use the bash tool to run exactly this command: "
                f"`{MISSING_BINARY} --version`. The command is expected to fail; "
                "do not retry it and do not run any other command. After the tool "
                f"call completes, reply with exactly {ANSWER_MARKER} and no other text."
            ),
        ],
        working_dir=PurePosixPath("/workspace"),
        timeout_seconds=180,
    )
    if result.exit_code != 0 and "insufficient_quota" in result.output:
        pytest.xfail(
            "Doppler OPENAI_API_KEY reached OpenAI through OpenCode but has insufficient quota"
        )
    assert result.exit_code == 0, result.output

    log_result = running_container.container.exec_run(
        ["/bin/sh", "-lc", f"cat {LOG_FILE}"]
    )
    log_output = _decode_output(log_result.output)
    assert log_result.exit_code == 0, (
        f"failed-commands log is missing after a failed bash command; "
        f"opencode run output: {result.output}"
    )

    records = [json.loads(line) for line in log_output.splitlines() if line.strip()]
    matching = [
        record
        for record in records
        if MISSING_BINARY in str(record.get("command")) and record.get("exit") == 127
    ]
    assert matching, (
        f"no failed-command record with exit 127 for {MISSING_BINARY}; "
        f"records: {records}; opencode run output: {result.output}"
    )
    record = matching[0]
    for field in ("timestamp", "sessionID", "callID", "output_tail"):
        assert record.get(field), f"record field {field} is empty: {record}"
    assert "not found" in record["output_tail"]


def _installed_probe_script() -> str:
    return "\n".join(
        [
            "set -eu",
            f"test -r {PLUGIN_FILE}",
            f"grep -q 'tool.execute.after' {PLUGIN_FILE}",
            "test -d /tmp/notes-assistant/command-monitor",
            "test -w /tmp/notes-assistant/command-monitor",
            (
                "health_response=$(wget -q -T 5 -O - "
                "http://127.0.0.1:4096/global/health 2>/dev/null) "
                "&& case \"$health_response\" in "
                "*'\"healthy\":true'*|*'\"healthy\": true'*) true ;; *) false ;; esac"
            ),
            "printf '%s\\n' command-monitor-installed",
        ]
    )


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


def _remove_volume_if_present(volume_name: str) -> None:
    import docker

    client = docker.from_env()
    try:
        client.volumes.get(volume_name).remove(force=True)
    except Exception:
        return None


def _docker_build_log(error: BaseException) -> str:
    build_log = getattr(error, "build_log", None)
    if not build_log:
        return "<docker build log is not available>"

    lines: list[str] = []
    for entry in _iter_build_log_entries(build_log):
        if isinstance(entry, dict):
            line = entry.get("stream") or entry.get("error") or repr(entry)
        else:
            line = repr(entry)
        lines.append(line.rstrip())
    return "\n".join(lines)


def _iter_build_log_entries(build_log: object) -> Iterable[object]:
    if isinstance(build_log, Iterable):
        return build_log
    return []
