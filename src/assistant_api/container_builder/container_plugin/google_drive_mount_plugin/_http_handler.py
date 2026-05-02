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

        def do_POST(self) -> None:
            plugin = self.server.plugin  # type: ignore[attr-defined]
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/import/local-folder":
                content_length = self.headers.get("Content-Length")
                if content_length is None:
                    self._send(400, "text/plain; charset=utf-8", "Content-Length is required.")
                    return
                try:
                    body_length = int(content_length)
                except ValueError:
                    self._send(400, "text/plain; charset=utf-8", "Content-Length is invalid.")
                    return
                body = self.rfile.read(body_length)
                self._send(
                    *plugin._import_local_folder(
                        self.headers.get("Content-Type", ""),
                        body,
                    )
                )
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
