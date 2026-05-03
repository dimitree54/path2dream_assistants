from __future__ import annotations

import json
import os
import threading
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from openai_provider_login_contract_helpers import (
    OpenAIProviderEnv,
    unused_port,
    write_openai_oauth_auth,
)


@dataclass(slots=True)
class OpenCodeProviderState:
    connected: bool = False
    include_openai_provider: bool = True
    include_headless_method: bool = True
    include_browser_method: bool = True
    unavailable: bool = False
    authorize_failure: bool = False
    callback_failure: bool = False
    callback_connects_auth: bool = True
    provider_requests: int = 0
    provider_request_auth_present: list[bool] = field(default_factory=list)
    auth_requests: int = 0
    authorize_requests: list[dict[str, Any]] = field(default_factory=list)
    callback_requests: list[dict[str, Any]] = field(default_factory=list)
    authorization_url: str = "https://auth.openai.com/codex/device"
    instructions: str = "Enter code: OPENAI-CONTRACT-CODE"

    def provider_payload(self) -> dict[str, Any]:
        providers = []
        if self.include_openai_provider:
            providers.append(
                {
                    "id": "openai",
                    "name": "OpenAI",
                    "source": "custom",
                    "env": [],
                    "options": {},
                    "models": {},
                }
            )
        return {
            "all": providers,
            "default": {},
            "connected": ["openai"] if self.connected else [],
        }

    def auth_payload(self) -> dict[str, Any]:
        methods: list[dict[str, str]] = []
        if self.include_browser_method:
            methods.append({"type": "oauth", "label": "ChatGPT Pro/Plus (browser)"})
        if self.include_headless_method:
            methods.append({"type": "oauth", "label": "ChatGPT Pro/Plus (headless)"})
        methods.append({"type": "api", "label": "Manually enter API Key"})
        return {"openai": methods}


@dataclass(slots=True)
class OpenCodeProviderStub:
    server: ThreadingHTTPServer
    thread: threading.Thread
    state: OpenCodeProviderState

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def close(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()


def _make_handler() -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: Any) -> None:
            return None

        def do_GET(self) -> None:
            state: OpenCodeProviderState = self.server.state  # type: ignore[attr-defined]
            parsed = urllib.parse.urlparse(self.path)
            if state.unavailable:
                self._send_json(503, {"error": "opencode unavailable"})
                return
            if parsed.path == "/global/health":
                self._send_json(200, {"healthy": True, "version": "contract"})
                return
            if parsed.path == "/provider":
                state.provider_requests += 1
                state.provider_request_auth_present.append(_openai_auth_file_exists())
                self._send_json(200, state.provider_payload())
                return
            if parsed.path == "/provider/auth":
                state.auth_requests += 1
                self._send_json(200, state.auth_payload())
                return
            self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:
            state: OpenCodeProviderState = self.server.state  # type: ignore[attr-defined]
            parsed = urllib.parse.urlparse(self.path)
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            payload = json.loads(body.decode("utf-8")) if body else {}
            if state.unavailable:
                self._send_json(503, {"error": "opencode unavailable"})
                return
            if parsed.path == "/provider/openai/oauth/authorize":
                state.authorize_requests.append(payload)
                if state.authorize_failure:
                    self._send_json(500, {"error": "authorize failed"})
                    return
                self._send_json(
                    200,
                    {
                        "url": state.authorization_url,
                        "method": "code",
                        "instructions": state.instructions,
                    },
                )
                return
            if parsed.path == "/provider/openai/oauth/callback":
                state.callback_requests.append(payload)
                if state.callback_failure:
                    self._send_json(500, {"error": "callback failed"})
                    return
                if state.callback_connects_auth:
                    state.connected = True
                    data_home = os.environ.get("XDG_DATA_HOME")
                    if not data_home:
                        self._send_json(500, {"error": "XDG_DATA_HOME is required"})
                        return
                    write_openai_oauth_auth(Path(data_home))
                    self._send_json(200, True)
                else:
                    self._send_json(200, False)
                return
            self._send_json(404, {"error": "not found"})

        def _send_json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _openai_auth_file_exists() -> bool:
    data_home = os.environ.get("XDG_DATA_HOME")
    if data_home:
        return (Path(data_home) / "opencode" / "auth.json").exists()
    home = os.environ.get("HOME")
    if home:
        return (Path(home) / ".local" / "share" / "opencode" / "auth.json").exists()
    return False


@pytest.fixture
def opencode_provider_stub() -> OpenCodeProviderStub:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler())
    server.state = OpenCodeProviderState()  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    stub = OpenCodeProviderStub(server=server, thread=thread, state=server.state)  # type: ignore[arg-type]
    try:
        yield stub
    finally:
        stub.close()


@pytest.fixture
def openai_provider_env(
    opencode_provider_stub: OpenCodeProviderStub,
) -> OpenAIProviderEnv:
    auth_port = unused_port()
    return OpenAIProviderEnv(
        opencode_api_port=opencode_provider_stub.port,
        openai_auth_port=auth_port,
    )
