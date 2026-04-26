from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from assistant_api.container_builder.container_plugin import OPENCODE_RUNTIME_STATE_KEY
from assistant_api.models import (
    ContainerRuntimeContext,
    ContainerSpec,
    ImageSpec,
    OpenCodeRuntimeMetadata,
)


REQUIRED_ENV = ("OPENCODE_API_PORT", "OPENAI_AUTH_PORT")
PROVIDER_ID = "openai"
VALID_STATUS_STATES = {"unavailable", "unauthenticated", "authenticated", "error"}


@dataclass(frozen=True, slots=True)
class OpenAIProviderEnv:
    opencode_api_port: int
    openai_auth_port: int


class OpenCodeRuntimeStatePlugin:
    name = "opencode-runtime-state"

    def __init__(self, api_container_port: int) -> None:
        self.api_container_port = api_container_port

    def configure_image(self, image: ImageSpec) -> None:
        return None

    def configure_container(self, container: ContainerSpec) -> None:
        container.state[OPENCODE_RUNTIME_STATE_KEY] = OpenCodeRuntimeMetadata(
            working_dir=PurePosixPath("/workspace"),
            api_container_port=self.api_container_port,
        )

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        return None


@dataclass(slots=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    url: str

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")

    def json(self) -> dict[str, Any]:
        return json.loads(self.text)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class FakeContainer:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def exec_run(self, command: list[str]) -> object:
        self.commands.append(command)

        class Result:
            exit_code = 0
            output = b""

        return Result()


def service_class() -> type[Any]:
    from assistant_api.container_builder.container_plugin.openai_provider_login_plugin import (
        OpenAIProviderLoginPluginService,
    )

    return OpenAIProviderLoginPluginService


def unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def opencode_api_port() -> int:
    return int(os.environ["OPENCODE_API_PORT"])


def openai_auth_port() -> int:
    return int(os.environ["OPENAI_AUTH_PORT"])


def read_url(
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    allow_redirects: bool = True,
    timeout: float = 5,
) -> HttpResponse:
    request = urllib.request.Request(url, data=data, headers=headers or {})
    opener = urllib.request.build_opener() if allow_redirects else urllib.request.build_opener(
        NoRedirect
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            return HttpResponse(
                status=response.status,
                headers=dict(response.headers),
                body=response.read(),
                url=response.url,
            )
    except urllib.error.HTTPError as error:
        return HttpResponse(
            status=error.code,
            headers=dict(error.headers),
            body=error.read(),
            url=url,
        )


def service_url(path: str, port: int | None = None) -> str:
    return f"http://127.0.0.1:{port or openai_auth_port()}{path}"


def opencode_url(path: str, port: int | None = None) -> str:
    return f"http://127.0.0.1:{port or opencode_api_port()}{path}"


def status_response(port: int | None = None) -> HttpResponse:
    return read_url(service_url("/status", port=port))


def status_json(port: int | None = None, expected_status: int = 200) -> dict[str, Any]:
    response = status_response(port=port)
    assert response.status == expected_status, response.text
    return response.json()


def wait_for_status_state(
    expected_state: str,
    *,
    port: int | None = None,
    timeout: float = 5,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_status: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        response = status_response(port=port)
        if response.headers.get("Content-Type", "").startswith("application/json"):
            last_status = response.json()
            if last_status.get("state") == expected_state:
                return last_status
        time.sleep(0.05)
    raise AssertionError(f"status never reached {expected_state}: {last_status}")


def extract_first_href(html: str) -> str:
    match = re.search(r'href=["\']([^"\']+)["\']', html)
    assert match is not None, html
    return match.group(1)


def start_plugin(plugin: object, state: dict[str, object] | None = None) -> ContainerRuntimeContext:
    runtime = ContainerRuntimeContext(
        docker_client=object(),
        container=FakeContainer(),
        state=state or {},
    )
    plugin.post_start(runtime)
    return runtime


def require_manual_env() -> None:
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise AssertionError(
            "manual OpenAI provider login test requires Doppler env vars: " + ", ".join(missing)
        )
