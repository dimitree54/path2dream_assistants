from __future__ import annotations

import html
import os
import time

import pytest

from assistant_api.container_builder import ContainerBuilderService
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


OPENCODE_TEST_MODEL = "openai/gpt-5.5"


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

        login = read_url(service_url("/login"))
        assert login.status == 200
        print(
            "\nOpen this OpenAI headless authorization page/code to authorize the manual test:\n"
            f"{login.text}\n"
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

        login = read_url(service_url("/login"))
        assert login.status == 200
        authorize_url = html.unescape(extract_first_href(login.text))
        print(
            "\nAuthorize the fresh OpenCode container with this OpenAI link:\n"
            f"{authorize_url}\n\n"
            "Login page shown by the auth service:\n"
            f"{login.text}\n\n"
            "After completing browser auth, leave this test running; it will poll /status "
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
                    "--model",
                    OPENCODE_TEST_MODEL,
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
