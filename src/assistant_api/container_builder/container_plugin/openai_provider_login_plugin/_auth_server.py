from __future__ import annotations

import json
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal

if __package__:
    from ._login_page import render_login_page
    from ._opencode_config import (
        OpenCodeConfigError,
        configure_default_model,
        validate_openai_opencode_model,
    )
else:
    from _login_page import render_login_page
    from _opencode_config import (  # type: ignore[no-redef]
        OpenCodeConfigError,
        configure_default_model,
        validate_openai_opencode_model,
    )


PROVIDER_ID = "openai"
PROVIDER_NAME = "OpenAI"
OpenAIAuthState = Literal["unavailable", "unauthenticated", "authenticated", "error"]


class OpenAIProviderLoginError(RuntimeError):
    pass


class OpenAIProviderAuthServer:
    def __init__(
        self,
        opencode_api_port: int,
        auth_port: int,
        opencode_model: str,
    ) -> None:
        if opencode_api_port == auth_port:
            raise OpenAIProviderLoginError(
                "OpenCode API port and OpenAI auth port must be different"
            )
        self.opencode_api_port = opencode_api_port
        self.auth_port = auth_port
        self.opencode_model = validate_openai_opencode_model(opencode_model)
        self.opencode_api_url = f"http://127.0.0.1:{opencode_api_port}"
        self.provider_name = PROVIDER_NAME
        self.headless_method_index: int | None = None
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._state: OpenAIAuthState = "unauthenticated"
        self._message = "OpenAI provider is not authenticated."
        self._auth_valid = False
        self._pending_authorize: dict[str, Any] | None = None
        self._callback_lock = threading.Lock()

    def start_in_thread(self, bind_host: str) -> None:
        self._validate_startup()
        self._server = ThreadingHTTPServer((bind_host, self.auth_port), self._handler_class())
        self._server.plugin = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def serve_forever(self, bind_host: str) -> None:
        self._validate_startup()
        self._server = ThreadingHTTPServer((bind_host, self.auth_port), self._handler_class())
        self._server.plugin = self  # type: ignore[attr-defined]
        self._server.serve_forever()

    def _validate_startup(self) -> None:
        self._request_json("GET", "/global/health", "OpenCode server is unavailable")
        auth_payload = self._request_json(
            "GET", "/provider/auth", "OpenCode provider auth list is unavailable"
        )
        self.headless_method_index = self._find_headless_method(auth_payload)

    def _login(self, query: dict[str, list[str]]) -> tuple[int, str, str]:
        status = self._status_payload()
        if status["state"] == "authenticated":
            return 200, "text/html; charset=utf-8", self._render_login_page(status)
        if self._pending_authorize is None:
            self._start_pending_authorization()
        if query.get("complete") == ["1"]:
            completed = self._complete_callback()
            status = self._status_payload()
            if status["state"] == "authenticated":
                return 200, "text/html; charset=utf-8", self._render_login_page(status)
            if not completed or status["state"] == "error":
                return 500, "text/html; charset=utf-8", self._render_login_page(status)
        status = self._status_payload()
        return 200, "text/html; charset=utf-8", self._render_login_page(status)

    def _start_pending_authorization(self) -> None:
        if self.headless_method_index is None:
            raise OpenAIProviderLoginError("OpenAI headless OAuth method was not initialized.")
        self._pending_authorize = self._request_json(
            "POST",
            f"/provider/{PROVIDER_ID}/oauth/authorize",
            "OpenAI headless OAuth authorize failed",
            {"method": self.headless_method_index},
        )
        self._state = "unauthenticated"
        self._message = "Waiting for OpenAI provider authorization."

    def _complete_callback(self) -> bool:
        if self._pending_authorize is None:
            raise OpenAIProviderLoginError("OpenAI authorization has not been started.")
        if not self._callback_lock.acquire(blocking=False):
            self._message = "Waiting for OpenAI provider authorization."
            return True
        try:
            result = self._request_json(
                "POST",
                f"/provider/{PROVIDER_ID}/oauth/callback",
                "OpenAI headless OAuth callback failed",
                {"method": self.headless_method_index},
                timeout=300,
            )
        except Exception as error:
            self._set_error(str(error))
            return False
        finally:
            self._callback_lock.release()
        if result is True:
            configure_default_model(self.opencode_model)
            self._pending_authorize = None
            status = self._status_payload()
            if status["authValid"] is not True:
                self._set_error(
                    "OpenAI authorization completed, but OpenCode auth credentials were not stored."
                )
                return False
        else:
            self._message = "Waiting for OpenAI provider authorization."
        return True

    def _status(self) -> tuple[int, str, str]:
        try:
            payload = self._status_payload()
            status = 200
        except Exception as error:
            payload = {
                "authValid": False,
                "state": "unavailable",
                "message": str(error),
                "providerName": self.provider_name,
            }
            status = 503
        return status, "application/json", json.dumps(payload)

    def _status_payload(self) -> dict[str, Any]:
        self._request_json("GET", "/global/health", "OpenCode server is unavailable")
        auth_method = _openai_auth_method()
        self._auth_valid = auth_method is not None
        if self._auth_valid:
            provider_payload = self._request_json(
                "GET", "/provider", "OpenCode provider status is unavailable"
            )
            provider = self._find_openai_provider(provider_payload)
            provider_name = provider.get("name")
            self.provider_name = provider_name if isinstance(provider_name, str) else PROVIDER_NAME
            self._state = "authenticated"
            self._message = f"OpenAI provider is authenticated through OpenCode {auth_method} credentials."
            self._pending_authorize = None
        elif self._state != "error":
            self._state = "unauthenticated"
            if self._pending_authorize is None:
                self._message = "OpenAI provider auth credentials are missing from OpenCode auth storage."
        return {
            "authValid": self._auth_valid,
            "state": self._state,
            "message": self._message,
            "providerName": self.provider_name,
        }

    def _request_json(
        self,
        method: str,
        path: str,
        error_message: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 5,
    ) -> Any:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            self.opencode_api_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data is not None else {},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if response.status < 200 or response.status >= 300:
                    raise OpenAIProviderLoginError(f"{error_message}: HTTP {response.status}")
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            error_body = error.read().decode("utf-8", errors="replace")
            error_detail = f": {error_body}" if error_body else ""
            raise OpenAIProviderLoginError(
                f"{error_message}: HTTP {error.code}{error_detail}"
            ) from error
        except urllib.error.URLError as error:
            raise OpenAIProviderLoginError(f"{error_message}: {error.reason}") from error
        except json.JSONDecodeError as error:
            raise OpenAIProviderLoginError(f"{error_message}: invalid JSON response") from error

    def _find_openai_provider(self, payload: Any) -> dict[str, Any]:
        providers = payload.get("all") if isinstance(payload, dict) else None
        if not isinstance(providers, list):
            raise OpenAIProviderLoginError("OpenCode /provider response does not contain provider list")
        for provider in providers:
            if isinstance(provider, dict) and provider.get("id") == PROVIDER_ID:
                return provider
        raise OpenAIProviderLoginError("OpenCode provider list does not contain openai provider")

    def _find_headless_method(self, payload: Any) -> int:
        methods = payload.get(PROVIDER_ID) if isinstance(payload, dict) else None
        if not isinstance(methods, list):
            raise OpenAIProviderLoginError("OpenCode /provider/auth response does not contain openai methods")
        for index, method in enumerate(methods):
            if not isinstance(method, dict):
                continue
            searchable_method = " ".join(
                str(method.get(key, "")) for key in ("id", "name", "label", "type")
            ).lower()
            if method.get("type") == "oauth" and "headless" in searchable_method:
                return index
        raise OpenAIProviderLoginError("OpenCode openai provider has no headless OAuth method")

    def _render_login_page(self, status: dict[str, Any]) -> str:
        return render_login_page(
            provider_name=self.provider_name,
            status=status,
            authorize_payload=self._pending_authorize,
        )

    def _set_error(self, message: str) -> None:
        self._auth_valid = False
        self._state = "error"
        self._message = message or "OpenAI provider login failed."

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format: str, *_args: Any) -> None:
                return None

            def do_GET(self) -> None:
                plugin: OpenAIProviderAuthServer = self.server.plugin  # type: ignore[attr-defined]
                parsed = urllib.parse.urlparse(self.path)
                try:
                    if parsed.path == "/login":
                        self._send(*plugin._login(urllib.parse.parse_qs(parsed.query)))
                        return
                    if parsed.path == "/status":
                        self._send(*plugin._status())
                        return
                    self._send(404, "text/plain; charset=utf-8", "Not found.")
                except Exception as error:
                    plugin._set_error(str(error))
                    self._send(500, "text/plain; charset=utf-8", plugin._message)

            def _send(self, status: int, content_type: str, body: str) -> None:
                payload = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        return Handler


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise OpenAIProviderLoginError(f"{name} is required")
    return value


def required_port_env(name: str) -> int:
    value = required_env(name)
    try:
        port = int(value)
    except ValueError as error:
        raise OpenAIProviderLoginError(f"{name} must be an integer TCP port") from error
    if port < 1 or port > 65535:
        raise OpenAIProviderLoginError(f"{name} must be an integer TCP port")
    return port


def _openai_auth_method() -> str | None:
    auth_info = _opencode_auth_records().get(PROVIDER_ID)
    if auth_info is None:
        return None
    if not isinstance(auth_info, dict):
        raise OpenAIProviderLoginError("OpenCode OpenAI auth credentials must be a JSON object")
    auth_type = auth_info.get("type")
    if auth_type == "oauth":
        if (
            _non_empty_string(auth_info.get("refresh"))
            and _non_empty_string(auth_info.get("access"))
            and isinstance(auth_info.get("expires"), int | float)
        ):
            return "oauth"
        raise OpenAIProviderLoginError("OpenCode OpenAI OAuth credentials are incomplete")
    raise OpenAIProviderLoginError("OpenCode OpenAI auth credentials have unsupported type")


def _opencode_auth_records() -> dict[str, Any]:
    auth_content = os.environ.get("OPENCODE_AUTH_CONTENT")
    if auth_content:
        source = "OPENCODE_AUTH_CONTENT"
        try:
            records = json.loads(auth_content)
        except json.JSONDecodeError as error:
            raise OpenAIProviderLoginError(f"{source} contains invalid JSON") from error
    else:
        source = str(_opencode_auth_path())
        path = Path(source)
        if not path.exists():
            return {}
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise OpenAIProviderLoginError(f"OpenCode auth file contains invalid JSON: {source}") from error
    if not isinstance(records, dict):
        raise OpenAIProviderLoginError(f"OpenCode auth records must be a JSON object: {source}")
    return records


def _opencode_auth_path() -> Path:
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return Path(xdg_data_home) / "opencode" / "auth.json"
    home = os.environ.get("HOME")
    if not home:
        raise OpenAIProviderLoginError("HOME or XDG_DATA_HOME is required")
    return Path(home) / ".local" / "share" / "opencode" / "auth.json"


def _non_empty_string(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


def main() -> None:
    try:
        server = OpenAIProviderAuthServer(
            opencode_api_port=required_port_env("OPENCODE_API_PORT"),
            auth_port=required_port_env("OPENAI_AUTH_PORT"),
            opencode_model=required_env("OPENCODE_MODEL"),
        )
        server.serve_forever("0.0.0.0")
    except (OpenAIProviderLoginError, OpenCodeConfigError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
