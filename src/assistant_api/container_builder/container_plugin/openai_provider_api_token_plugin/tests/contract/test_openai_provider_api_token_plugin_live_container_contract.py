from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from uuid import uuid4

import pytest

from assistant_api.container_builder import (
    ContainerBuilderService,
    RunningContainerCommandRunnerService,
)
from assistant_api.container_builder.container_plugin.local_dir_mount_plugin import (
    LocalDirMountPluginService,
)
from assistant_api.container_builder.container_plugin.opencode_persistence_plugin import (
    OpenCodePersistencePluginService,
)
from assistant_api.container_builder.container_plugin.opencode_server_plugin import (
    OpenCodeServerPluginService,
)
from assistant_api.container_builder.container_plugin.openai_provider_api_token_plugin import (
    OpenAIProviderApiTokenPluginService,
)
from openai_provider_api_token_contract_helpers import TOKEN_ENV_VAR, unused_port


LIVE_MODEL = "openai/gpt-4.1-mini"
ANSWER_MARKER = "OPENCODE_API_TOKEN_OK"


@pytest.mark.live_container
def test_live_container_persists_openai_api_token_auth_and_opencode_answers(
    tmp_path: Path,
) -> None:
    _require_openai_live_account()

    suffix = f"{os.getpid()}-{uuid4().hex[:8]}"
    config_volume = f"openai_api_token_config_{suffix}"
    data_volume = f"openai_api_token_data_{suffix}"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first_image_tag = f"notes-assistant-openai-api-token-{suffix}:first"
    second_image_tag = f"notes-assistant-openai-api-token-{suffix}:second"

    first_builder = _builder(
        suffix=f"{suffix}-first",
        image_tag=first_image_tag,
        workspace=workspace,
        config_volume=config_volume,
        data_volume=data_volume,
        start_server=True,
        plugins=[
            OpenAIProviderApiTokenPluginService(
                api_token_env_var=TOKEN_ENV_VAR,
                opencode_model=LIVE_MODEL,
            )
        ],
    )
    second_builder = _builder(
        suffix=f"{suffix}-second",
        image_tag=second_image_tag,
        workspace=workspace,
        config_volume=config_volume,
        data_volume=data_volume,
        start_server=False,
        plugins=[],
    )

    try:
        first_running = _build_and_run_or_fail(first_builder)
        try:
            _assert_auth_installed_with_exact_container_env_token(first_running.container)
        finally:
            first_builder.stop(remove=True)

        second_running = _build_and_run_or_fail(second_builder)
        try:
            _assert_persisted_auth_loaded_without_token_env(second_running.container)
            _assert_opencode_answers_with_persisted_auth(second_running)
        finally:
            second_builder.stop(remove=True)
    finally:
        _stop_builder_if_started(first_builder)
        _stop_builder_if_started(second_builder)
        _remove_image_if_present(first_image_tag)
        _remove_image_if_present(second_image_tag)
        _remove_volume_if_present(config_volume)
        _remove_volume_if_present(data_volume)
        _remove_volume_if_present(f"{config_volume}_auth")
        _remove_volume_if_present(f"{data_volume}_auth")


def _builder(
    *,
    suffix: str,
    image_tag: str,
    workspace: Path,
    config_volume: str,
    data_volume: str,
    start_server: bool,
    plugins: list[object],
) -> ContainerBuilderService:
    server_plugins: list[object] = []
    if start_server:
        server_plugins.append(OpenCodeServerPluginService(host_port=unused_port()))

    return ContainerBuilderService(
        plugins=[
            OpenCodePersistencePluginService(
                config_volume=config_volume,
                data_volume=data_volume,
                persist_auth=True,
                persist_chat_history=False,
                persist_opencode_artifacts=False,
                persist_skills=False,
                persist_agents=False,
            ),
            LocalDirMountPluginService(workspace),
            *server_plugins,
            *plugins,
        ],
        container_name=f"notes-assistant-openai-token-{suffix}",
        image_tag=image_tag,
    )


def _build_and_run_or_fail(builder: ContainerBuilderService) -> object:
    try:
        return builder.build_and_run()
    except Exception as error:
        _stop_builder_if_started(builder)
        pytest.fail(
            "OpenAI API-token plugin live container failed before probe; "
            f"got {type(error).__name__}: {error}\n\n{_docker_build_log(error)}"
        )


def _require_openai_live_account() -> None:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        pytest.skip("OpenAI paid-account live probe is validated locally under Doppler")
    if not os.environ.get(TOKEN_ENV_VAR):
        pytest.skip("OPENAI_API_KEY is required for OpenAI API-token live container test")


def _assert_auth_installed_with_exact_container_env_token(container: object) -> None:
    result = container.exec_run(["/bin/sh", "-lc", _exact_token_probe_script()])
    output = _decode_output(result.output)
    assert result.exit_code == 0, output
    assert "openai-api-token-auth-installed" in output


def _assert_persisted_auth_loaded_without_token_env(container: object) -> None:
    result = container.exec_run(["/bin/sh", "-lc", _persisted_auth_probe_script()])
    output = _decode_output(result.output)
    assert result.exit_code == 0, output
    assert "openai-api-token-auth-persisted" in output


def _assert_opencode_answers_with_persisted_auth(running_container: object) -> None:
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
            f"Reply with exactly {ANSWER_MARKER} and no other text.",
        ],
        working_dir=PurePosixPath("/workspace"),
        timeout_seconds=180,
    )

    if result.exit_code != 0 and "insufficient_quota" in result.output:
        pytest.xfail(
            "Doppler OPENAI_API_KEY reached OpenAI through OpenCode but has insufficient quota"
        )
    assert result.exit_code == 0, result.output
    assert ANSWER_MARKER in result.output


def _exact_token_probe_script() -> str:
    return "\n".join(
        [
            "set -eu",
            "test -L /root/.local/share/opencode/auth.json",
            "test -r /tmp/notes-assistant/opencode-persistence/auth/auth.json",
            "python3 - <<'PY'",
            "import json",
            "import os",
            "from pathlib import Path",
            "auth_path = Path('/tmp/notes-assistant/opencode-persistence/auth/auth.json')",
            "auth = json.loads(auth_path.read_text())",
            "assert auth['openai']['type'] == 'api'",
            "assert auth['openai']['key'] == os.environ['OPENAI_API_KEY']",
            "config_path = Path('/root/.config/opencode/opencode.json')",
            "config = json.loads(config_path.read_text())",
            f"assert config['model'] == {LIVE_MODEL!r}",
            "PY",
            "printf '%s\\n' openai-api-token-auth-installed",
        ]
    )


def _persisted_auth_probe_script() -> str:
    return "\n".join(
        [
            "set -eu",
            "test -z \"${OPENAI_API_KEY:-}\"",
            "test -L /root/.local/share/opencode/auth.json",
            "test -r /tmp/notes-assistant/opencode-persistence/auth/auth.json",
            "grep -q '\"openai\"' /tmp/notes-assistant/opencode-persistence/auth/auth.json",
            "grep -q '\"type\": \"api\"' /tmp/notes-assistant/opencode-persistence/auth/auth.json",
            "printf '%s\\n' openai-api-token-auth-persisted",
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
