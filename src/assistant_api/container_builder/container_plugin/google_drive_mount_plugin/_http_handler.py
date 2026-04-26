from __future__ import annotations

import urllib.parse
from http.server import BaseHTTPRequestHandler
from typing import Any


def google_drive_mount_handler_class() -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: Any) -> None:
            return None

        def do_GET(self) -> None:
            plugin = self.server.plugin  # type: ignore[attr-defined]
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/login":
                self._send(*plugin._login())
                return
            if parsed.path == "/oauth/callback":
                self._send(*plugin._oauth_callback(query))
                return
            if parsed.path == "/logout":
                self._send(*plugin._logout())
                return
            if parsed.path == "/status":
                self._send(*plugin._status())
                return
            self._send(404, "text/plain; charset=utf-8", "Not found.")

        def _send(self, status: int, content_type: str, body: str) -> None:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return Handler
