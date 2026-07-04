from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from uuid import uuid4

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin.local_dir_mount_plugin import (
    LocalDirMountPluginService,
)
from assistant_api.container_builder.container_plugin.opencode_persistence_plugin import (
    OpenCodePersistencePluginService,
)
from assistant_api.container_builder.container_plugin.opencode_server_plugin import (
    OpenCodeServerPluginService,
)
from assistant_api.container_builder.container_plugin.openai_provider_auth_rotation_plugin import (
    OpenAIProviderAuthRotationPluginService,
)
from openai_provider_auth_rotation_contract_helpers import (
    TOKEN_ENV_VAR,
    api_auth,
    unused_port,
    write_auth_file,
)


LIVE_MODEL = "openai/gpt-5.4-mini"


@pytest.mark.live_container
def test_live_container_selects_candidate_auth_and_reruns_selection_on_recreate(
    tmp_path: Path,
) -> None:
    api_key = _require_openai_api_key()
    suffix = f"{os.getpid()}-{uuid4().hex[:8]}"
    image_tag = f"notes-assistant-openai-auth-rotation-{suffix}:latest"
    config_volume = f"openai_auth_rotation_config_{suffix}"
    data_volume = f"openai_auth_rotation_data_{suffix}"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first_candidate = write_auth_file(
        tmp_path / "first" / "auth.json",
        api_auth(api_key, source="candidate-one"),
    )
    second_candidate = write_auth_file(
        tmp_path / "second" / "auth.json",
        api_auth(api_key, source="candidate-two"),
    )

    first_builder = _builder(
        suffix=f"{suffix}-first",
        image_tag=image_tag,
        config_volume=config_volume,
        data_volume=data_volume,
        workspace=workspace,
        candidate_auth_files=[first_candidate],
        start_server=True,
        build_policy="always",
    )
    persisted_builder = _builder(
        suffix=f"{suffix}-persisted",
        image_tag=image_tag,
        config_volume=config_volume,
        data_volume=data_volume,
        workspace=workspace,
        candidate_auth_files=None,
        start_server=False,
        build_policy="if_missing",
    )
    second_builder = _builder(
        suffix=f"{suffix}-second",
        image_tag=image_tag,
        config_volume=config_volume,
        data_volume=data_volume,
        workspace=workspace,
        candidate_auth_files=[second_candidate],
        start_server=False,
        build_policy="if_missing",
    )

    try:
        first_running = _build_and_run_or_fail(first_builder)
        try:
            _assert_active_source(first_running.container, "candidate-one")
            _assert_opencode_server_healthy(first_running.container)
        finally:
            first_builder.stop(remove=True)

        persisted_running = _build_and_run_or_fail(persisted_builder)
        try:
            _assert_active_source(persisted_running.container, "candidate-one")
        finally:
            persisted_builder.stop(remove=True)

        second_running = _build_and_run_or_fail(second_builder)
        try:
            _assert_active_source(second_running.container, "candidate-two")
        finally:
            second_builder.stop(remove=True)
    finally:
        for builder in (first_builder, persisted_builder, second_builder):
            _stop_builder_if_started(builder)
        _remove_image_if_present(image_tag)
        _remove_volume_if_present(f"{data_volume}_auth")


@pytest.mark.live_container
def test_live_container_falls_back_to_openai_api_key_after_bad_candidate(
    tmp_path: Path,
) -> None:
    _require_openai_api_key()
    suffix = f"{os.getpid()}-{uuid4().hex[:8]}"
    image_tag = f"notes-assistant-openai-auth-rotation-fallback-{suffix}:latest"
    config_volume = f"openai_auth_rotation_fallback_config_{suffix}"
    data_volume = f"openai_auth_rotation_fallback_data_{suffix}"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bad_candidate = write_auth_file(
        tmp_path / "bad" / "auth.json",
        api_auth("sk-invalid-openai-auth-rotation-live-test", source="bad-candidate"),
    )
    builder = _builder(
        suffix=suffix,
        image_tag=image_tag,
        config_volume=config_volume,
        data_volume=data_volume,
        workspace=workspace,
        candidate_auth_files=[bad_candidate],
        start_server=False,
        build_policy="always",
    )

    try:
        running = _build_and_run_or_fail(builder)
        try:
            _assert_fallback_auth(running.container)
        finally:
            builder.stop(remove=True)
    finally:
        _stop_builder_if_started(builder)
        _remove_image_if_present(image_tag)
        _remove_volume_if_present(f"{data_volume}_auth")


def _builder(
    *,
    suffix: str,
    image_tag: str,
    config_volume: str,
    data_volume: str,
    workspace: Path,
    candidate_auth_files: list[Path] | None,
    start_server: bool,
    build_policy: str,
) -> ContainerBuilderService:
    plugins: list[object] = [
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
    ]
    if start_server:
        plugins.append(OpenCodeServerPluginService(host_port=unused_port()))
    if candidate_auth_files is not None:
        plugins.append(
            OpenAIProviderAuthRotationPluginService(
                candidate_auth_files=candidate_auth_files,
                opencode_model=LIVE_MODEL,
                probe_model=LIVE_MODEL,
                probe_variant="low",
                probe_message="hi",
                probe_timeout_seconds=180,
            )
        )
    return ContainerBuilderService(
        plugins=plugins,
        container_name=f"notes-assistant-openai-auth-rotation-{suffix}",
        image_tag=image_tag,
        build_policy=build_policy,
    )


def _assert_active_source(container: object, expected_source: str) -> None:
    result = container.exec_run(
        [
            "/bin/sh",
            "-lc",
            _source_probe_script(expected_source),
        ]
    )
    output = _decode_output(result.output)
    assert result.exit_code == 0, output
    assert f"active-source:{expected_source}" in output


def _assert_fallback_auth(container: object) -> None:
    result = container.exec_run(["/bin/sh", "-lc", _fallback_probe_script()])
    output = _decode_output(result.output)
    assert result.exit_code == 0, output
    assert "fallback-auth-active" in output


def _assert_opencode_server_healthy(container: object) -> None:
    result = container.exec_run(
        [
            "/bin/sh",
            "-lc",
            (
                "wget -qO- http://127.0.0.1:4096/global/health "
                "| grep -q '\"healthy\"[[:space:]]*:[[:space:]]*true'"
            ),
        ]
    )
    output = _decode_output(result.output)
    assert result.exit_code == 0, output


def _source_probe_script(expected_source: str) -> str:
    return "\n".join(
        [
            "set -eu",
            "python3 - <<'PY'",
            "import json",
            "from pathlib import Path",
            "auth_path = Path('/tmp/notes-assistant/opencode-persistence/auth/auth.json')",
            "auth = json.loads(auth_path.read_text())",
            "assert auth['openai']['type'] == 'api'",
            f"assert auth['openai']['metadata']['source'] == {expected_source!r}",
            "PY",
            f"printf '%s\\n' active-source:{expected_source}",
        ]
    )


def _fallback_probe_script() -> str:
    return "\n".join(
        [
            "set -eu",
            "python3 - <<'PY'",
            "import json",
            "import os",
            "from pathlib import Path",
            "auth_path = Path('/tmp/notes-assistant/opencode-persistence/auth/auth.json')",
            "auth = json.loads(auth_path.read_text())",
            "assert auth['openai']['type'] == 'api'",
            "assert auth['openai']['key'] == os.environ['OPENAI_API_KEY']",
            "assert auth['openai'].get('metadata') is None",
            "PY",
            "printf '%s\\n' fallback-auth-active",
        ]
    )


def _build_and_run_or_fail(builder: ContainerBuilderService) -> object:
    try:
        return builder.build_and_run()
    except Exception as error:
        _stop_builder_if_started(builder)
        pytest.fail(
            "OpenAI auth rotation live container failed before probe; "
            f"got {type(error).__name__}: {error}\n\n{_docker_build_log(error)}"
        )


def _require_openai_api_key() -> str:
    value = os.environ.get(TOKEN_ENV_VAR)
    if not value:
        pytest.skip("OPENAI_API_KEY is required for OpenAI auth rotation live tests")
    return value


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
