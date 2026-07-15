from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder import RunningContainerCommandRunnerService
from assistant_api.container_builder.container_plugin.local_dir_mount_plugin import (
    LocalDirMountPluginService,
)
from assistant_api.container_builder.container_plugin.opencode_persistence_plugin import (
    OpenCodePersistencePluginService,
)
from assistant_api.container_builder.container_plugin.opencode_server_plugin import (
    OpenCodeServerPluginService,
)
from controlled_parallel_provider import (
    ControlledParallelProviderPlugin,
    STATE_PATH,
)


@pytest.mark.live_container
def test_live_container_parallel_subagents_preserve_reopenable_host_history(
    tmp_path: Path,
) -> None:
    suffix = f"{os.getpid()}-{uuid4().hex[:8]}"
    image_tag = f"notes-assistant-opencode-host-history-{suffix}:test"
    history_dir = tmp_path / "history"
    workspace = tmp_path / "workspace"
    history_dir.mkdir()
    workspace.mkdir()
    (workspace / "opencode.json").write_text(
        '{"provider":{"openai":{"options":{"baseURL":'
        '"http://127.0.0.1:18080/v1","apiKey":"test"}}}}',
        encoding="utf-8",
    )
    session_title = f"host-history-{suffix}"

    first_port = _unused_port()
    second_port = _unused_port()
    first_builder = _builder(
        suffix=f"{suffix}-first",
        image_tag=image_tag,
        workspace=workspace,
        history_dir=history_dir,
        host_port=first_port,
        build_policy="always",
    )
    second_builder = _builder(
        suffix=f"{suffix}-second",
        image_tag=image_tag,
        workspace=workspace,
        history_dir=history_dir,
        host_port=second_port,
        build_policy="never",
    )

    try:
        first_running = _build_and_run_or_fail(first_builder)
        try:
            session_id = _create_session(first_port, session_title)
            _send_message(
                first_port,
                session_id,
                "Launch exactly five parallel general sub-agents and then finish.",
            )
            state_result = RunningContainerCommandRunnerService(first_running).run_command(
                [
                    "/bin/sh",
                    "-lc",
                    (
                        "attempts=0; until grep -q '\"child_released\": 5' "
                        f"{STATE_PATH}; do attempts=$((attempts + 1)); "
                        f"if [ \"$attempts\" -ge 240 ]; then cat {STATE_PATH}; exit 1; fi; "
                        "sleep 0.25; done; "
                        f"cat {STATE_PATH}"
                    ),
                ],
                timeout_seconds=70,
            )
            assert state_result.exit_code == 0, state_result.output
            provider_state = json.loads(state_result.output)
            assert provider_state == {
                "barrier_passed": True,
                "child_released": 5,
                "child_started": 5,
                "tasks_issued": 5,
            }
            _assert_session_messages_include(
                first_port,
                session_id,
                "Launch exactly five parallel general sub-agents and then finish.",
                "PARENT_PARALLEL_OK",
            )
        finally:
            first_builder.stop(remove=True)

        _assert_stopped_database_is_delete_and_integrity_ok(image_tag, history_dir)

        _build_and_run_or_fail(second_builder)
        try:
            _assert_session_listed(second_port, session_id)
            _send_message(second_port, session_id, "Reply exactly REOPENED_OK")
            _assert_session_messages_include(
                second_port,
                session_id,
                "PARENT_PARALLEL_OK",
                "Reply exactly REOPENED_OK",
                "REOPENED_OK",
            )
        finally:
            second_builder.stop(remove=True)
    finally:
        _stop_builder_if_started(first_builder)
        _stop_builder_if_started(second_builder)
        _remove_image_if_present(image_tag)

    history_files = list(history_dir.rglob("*"))
    assert any(path.name.startswith("opencode.db") for path in history_files)
    assert all(path.name != "auth.json" for path in history_files)


@pytest.mark.live_container
def test_live_container_rejects_corrupt_host_history_before_opencode_starts(
    tmp_path: Path,
) -> None:
    suffix = f"{os.getpid()}-{uuid4().hex[:8]}"
    image_tag = f"notes-assistant-opencode-corrupt-history-{suffix}:test"
    history_dir = tmp_path / "history"
    workspace = tmp_path / "workspace"
    history_dir.mkdir()
    workspace.mkdir()
    database = history_dir / "opencode.db"
    original = b"not-a-sqlite-database"
    database.write_bytes(original)
    builder = _builder(
        suffix=suffix,
        image_tag=image_tag,
        workspace=workspace,
        history_dir=history_dir,
        host_port=_unused_port(),
        build_policy="always",
        controlled_provider=False,
    )

    try:
        with pytest.raises(RuntimeError, match="host-history integrity check failed"):
            builder.build_and_run()
    finally:
        _stop_builder_if_started(builder)
        _remove_image_if_present(image_tag)

    assert database.read_bytes() == original
    assert not (history_dir / "opencode.db-wal").exists()
    assert not (history_dir / "opencode.db-shm").exists()


def _builder(
    *,
    suffix: str,
    image_tag: str,
    workspace: Path,
    history_dir: Path,
    host_port: int,
    build_policy: str,
    controlled_provider: bool = True,
) -> ContainerBuilderService:
    plugins = []
    if controlled_provider:
        plugins.append(ControlledParallelProviderPlugin())
    plugins.extend(
        [
            OpenCodePersistencePluginService(
                config_volume=f"unused_host_history_config_{suffix}",
                data_volume=f"unused_host_history_data_{suffix}",
                persist_auth=False,
                persist_chat_history=True,
                persist_opencode_artifacts=False,
                persist_skills=False,
                persist_agents=False,
                chat_history_host_dir=history_dir,
            ),
            LocalDirMountPluginService(workspace),
            OpenCodeServerPluginService(host_port=host_port),
        ]
    )
    return ContainerBuilderService(
        plugins=plugins,
        container_name=f"notes-assistant-opencode-host-history-{suffix}",
        image_tag=image_tag,
        build_policy=build_policy,
    )


def _build_and_run_or_fail(builder: ContainerBuilderService):
    try:
        return builder.build_and_run()
    except Exception as error:
        _stop_builder_if_started(builder)
        pytest.fail(
            "OpenCode persistence host-history live container failed before probe; "
            f"got {type(error).__name__}: {error}"
        )


def _assert_stopped_database_is_delete_and_integrity_ok(
    image_tag: str,
    history_dir: Path,
) -> None:
    import docker

    output = docker.from_env().containers.run(
        image_tag,
        [
            "sqlite3",
            "-readonly",
            "/history/opencode.db",
            "PRAGMA journal_mode; PRAGMA integrity_check;",
        ],
        entrypoint="",
        volumes={str(history_dir): {"bind": "/history", "mode": "rw"}},
        remove=True,
    )
    assert output.decode().splitlines() == ["delete", "ok"]


def _create_session(port: int, title: str) -> str:
    payload = _json_request(port, "POST", "/session", {"title": title})
    session_id = payload.get("id") if isinstance(payload, dict) else None
    assert isinstance(session_id, str) and session_id, payload
    return session_id


def _send_message(port: int, session_id: str, text: str) -> None:
    payload = _json_request(
        port,
        "POST",
        f"/session/{session_id}/message",
        {"parts": [{"type": "text", "text": text}]},
        timeout=120,
    )
    assert isinstance(payload, dict), payload


def _assert_session_listed(port: int, session_id: str) -> None:
    payload = _json_request(port, "GET", "/session")
    assert isinstance(payload, list), payload
    assert any(
        isinstance(session, dict) and session.get("id") == session_id
        for session in payload
    ), payload


def _assert_session_messages_include(
    port: int,
    session_id: str,
    *expected_texts: str,
) -> None:
    payload = _json_request(port, "GET", f"/session/{session_id}/message")
    assert isinstance(payload, list), payload
    texts = [
        part.get("text")
        for message in payload
        if isinstance(message, dict)
        for part in message.get("parts", [])
        if isinstance(part, dict)
    ]
    for expected_text in expected_texts:
        assert expected_text in texts


def _json_request(
    port: int,
    method: str,
    path: str,
    payload: dict[str, object] | None = None,
    *,
    timeout: int = 30,
) -> object:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            assert response.status == 200
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        pytest.fail(f"OpenCode API returned HTTP {error.code}: {body[:1000]}")


def _unused_port() -> int:
    server = socket.socket()
    try:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])
    finally:
        server.close()


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
