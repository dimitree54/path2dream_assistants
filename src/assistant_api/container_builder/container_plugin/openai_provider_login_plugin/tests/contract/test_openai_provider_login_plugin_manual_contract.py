from __future__ import annotations

import html
import json
import os
import time
from typing import Any

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin.opencode_persistence_plugin import (
    OpenCodePersistencePluginService,
)
from assistant_api.container_builder.container_plugin.opencode_server_plugin import (
    OpenCodeServerPluginService,
)
from assistant_api.container_builder.container_plugin.opencode_web_server_plugin import (
    OpenCodeWebServerPluginService,
)
from openai_provider_login_contract_helpers import (
    extract_first_href,
    openai_auth_port,
    opencode_api_port,
    opencode_url,
    read_url,
    require_manual_env,
    service_class,
    service_url,
    status_json,
)


@pytest.mark.manual
def test_manual_live_openai_provider_headless_login_round_trip() -> None:
    require_manual_env()
    plugin = service_class()(host_port=openai_auth_port())
    builder = ContainerBuilderService(
        plugins=[
            OpenCodeWebServerPluginService(
                host_port=opencode_api_port(),
                container_port=opencode_api_port(),
            ),
            plugin,
        ],
        container_name=f"notes-assistant-openai-login-manual-{os.getpid()}",
    )
    running = builder.build_and_run()
    try:
        status = _wait_for_status()
        assert {"authValid", "state", "message", "providerName"} <= set(status)

        inside_status = running.container.exec_run(
            [
                "/bin/sh",
                "-lc",
                (
                    f"curl -fsS http://127.0.0.1:{openai_auth_port()}/status "
                    f"|| wget -qO- http://127.0.0.1:{openai_auth_port()}/status"
                ),
            ]
        )
        assert inside_status.exit_code == 0, inside_status.output

        login_url = service_url("/login")
        login = read_url(login_url)
        assert login.status == 200
        print(
            "\nOpen this OpenAI login page in a browser to authorize the manual test:\n"
            f"{login_url}\n"
        )

        deadline = time.monotonic() + 300
        status = status_json()
        while status["state"] != "authenticated" and time.monotonic() < deadline:
            time.sleep(2)
            status = _wait_for_status()
        assert status["state"] == "authenticated"
        assert status["authValid"] is True

        _assert_opencode_auth_list_has_openai(running)

        _assert_opencode_can_use_authenticated_openai(running)
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_manual_fresh_container_openai_login_then_real_opencode_prompt() -> None:
    require_manual_env()
    plugin = service_class()(host_port=openai_auth_port())
    builder = ContainerBuilderService(
        plugins=[
            OpenCodeWebServerPluginService(
                host_port=opencode_api_port(),
                container_port=opencode_api_port(),
            ),
            plugin,
        ],
        container_name=f"notes-assistant-openai-prompt-manual-{os.getpid()}",
    )
    running = builder.build_and_run()
    try:
        status = _wait_for_status()
        assert status["authValid"] is False
        assert status["state"] == "unauthenticated"

        provider_response = read_url(opencode_url("/provider"))
        assert provider_response.status == 200
        assert "openai" not in provider_response.json()["connected"]

        login_url = service_url("/login")
        login = read_url(login_url)
        assert login.status == 200
        authorize_url = html.unescape(extract_first_href(login.text))
        print(
            "\nOpen this login page in a browser and leave it open until success:\n"
            f"{login_url}\n\n"
            "Direct OpenAI authorization link from the page:\n"
            f"{authorize_url}\n\n"
            "Login page HTML returned by the auth service:\n"
            f"{login.text}\n\n"
            "After completing browser auth, leave this test running; the page and test will poll /status "
            "and then run a real OpenCode prompt.\n"
        )

        deadline = time.monotonic() + 300
        status = status_json()
        while status["state"] != "authenticated" and time.monotonic() < deadline:
            time.sleep(2)
            status = _wait_for_status()
        assert status["state"] == "authenticated"
        assert status["authValid"] is True

        _assert_opencode_auth_list_has_openai(running)

        _assert_opencode_can_use_authenticated_openai(running)
    finally:
        builder.stop(remove=True)


@pytest.mark.manual
def test_manual_live_opencode_server_persistence_reuses_openai_auth_and_runs_real_api_prompt() -> None:
    require_manual_env()
    if opencode_api_port() == openai_auth_port():
        raise AssertionError("manual test requires OPENCODE_API_PORT and OPENAI_AUTH_PORT to differ")

    container_name = f"notes-assistant-openai-server-persistence-manual-{os.getpid()}"

    first_builder = ContainerBuilderService(
        plugins=_opencode_server_stack_plugins(),
        container_name=container_name,
    )
    first_running = first_builder.build_and_run()
    try:
        _wait_for_opencode_health()
        status = _wait_for_status()
        _ensure_openai_login(status)
        _assert_opencode_auth_list_has_openai(first_running)
    finally:
        first_builder.stop(remove=True)

    second_builder = ContainerBuilderService(
        plugins=_opencode_server_stack_plugins(),
        container_name=container_name,
    )
    second_running = second_builder.build_and_run()
    try:
        _wait_for_opencode_health()
        status = _wait_for_status_state("authenticated", timeout=120)
        assert status["authValid"] is True

        session_id = _create_opencode_session("manual-openai-persistence-e2e")
        response_text = _send_opencode_prompt_and_collect_text(
            session_id,
            "Hi, I am testing you. Answer with the word abracadabra and no other text.",
        )
        assert "abracadabra" in response_text.lower(), response_text
    finally:
        second_builder.stop(remove=True)


def _assert_opencode_auth_list_has_openai(running: object) -> None:
    result = running.container.exec_run(["opencode", "auth", "list"])  # type: ignore[attr-defined]
    output = result.output.decode("utf-8", errors="replace")
    assert result.exit_code == 0, output
    assert "OpenAI" in output, output


def _assert_opencode_can_use_authenticated_openai(running: object) -> None:
    result = running.container.exec_run(  # type: ignore[attr-defined]
        [
            "/bin/sh",
            "-lc",
            " ".join(
                [
                    "opencode",
                    "run",
                    "--format",
                    "json",
                    "--dir",
                    "/workspace",
                    "'Reply with exactly: hi'",
                ]
            ),
        ]
    )
    output = result.output.decode("utf-8", errors="replace")
    assert result.exit_code == 0, output
    assert "hi" in output.lower(), output


def _wait_for_opencode_health(timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = read_url(opencode_url("/global/health"), timeout=2)
            if response.status == 200:
                payload = response.json()
                if payload.get("healthy") is True:
                    return
                last_error = AssertionError(payload)
            else:
                last_error = AssertionError(response.text)
        except Exception as error:
            last_error = error
        time.sleep(1)
    raise AssertionError(f"OpenCode /global/health did not become ready: {last_error}")


def _wait_for_status(timeout: float = 60) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = read_url(service_url("/status"), timeout=2)
            if response.status == 200:
                return response.json()
            last_error = AssertionError(response.text)
        except Exception as error:
            last_error = error
        time.sleep(1)
    raise AssertionError(f"OpenAI auth status endpoint did not become ready: {last_error}")


def _wait_for_status_state(expected_state: str, timeout: float = 300) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    last_status: dict[str, object] | None = None
    while time.monotonic() < deadline:
        last_status = _wait_for_status(timeout=10)
        if last_status.get("state") == expected_state:
            return last_status
        time.sleep(2)
    raise AssertionError(
        f"OpenAI auth status never reached {expected_state}: last_status={last_status}"
    )


def _ensure_openai_login(status: dict[str, object]) -> None:
    if status.get("state") == "authenticated":
        print(
            "\nOpenAI provider is already authenticated from persisted OpenCode state. "
            "Skipping browser login for this run.\n"
        )
        return

    login_url = service_url("/login")
    login = read_url(login_url)
    assert login.status == 200
    authorize_url = html.unescape(extract_first_href(login.text))
    print(
        "\nOpen this login page in a browser and finish OpenAI authorization:\n"
        f"{login_url}\n\n"
        "Direct OpenAI authorization link from the page:\n"
        f"{authorize_url}\n"
    )
    authenticated = _wait_for_status_state("authenticated")
    assert authenticated["authValid"] is True


def _opencode_server_stack_plugins() -> list[object]:
    return [
        OpenCodePersistencePluginService(),
        OpenCodeServerPluginService(
            host_port=opencode_api_port(),
            container_port=opencode_api_port(),
        ),
        service_class()(host_port=openai_auth_port()),
    ]


def _create_opencode_session(title: str) -> str:
    response = _post_json(opencode_url("/session"), {"title": title})
    assert response.status == 200, response.text
    payload = response.json()
    session_id = payload.get("id")
    assert isinstance(session_id, str) and session_id, payload
    return session_id


def _send_opencode_prompt_and_collect_text(session_id: str, prompt: str) -> str:
    response = _post_json(
        opencode_url(f"/session/{session_id}/message"),
        {
            "parts": [
                {
                    "type": "text",
                    "text": prompt,
                }
            ]
        },
    )
    assert response.status == 200, response.text
    payload = response.json()
    return _text_parts(payload)


def _text_parts(payload: dict[str, Any]) -> str:
    parts = payload.get("parts")
    assert isinstance(parts, list), payload
    text = "\n".join(
        part["text"]
        for part in parts
        if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
    )
    assert text, payload
    return text


def _post_json(url: str, payload: dict[str, Any]) -> Any:
    return read_url(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        timeout=60,
    )
