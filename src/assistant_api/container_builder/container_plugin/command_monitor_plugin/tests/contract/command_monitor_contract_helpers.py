from __future__ import annotations

import socket
from typing import Any


class RecordingContainer:
    def __init__(self, *, exit_code: int = 0, output: bytes = b"") -> None:
        self.exit_code = exit_code
        self.output = output
        self.commands: list[list[str]] = []

    def exec_run(self, command: list[str]) -> object:
        self.commands.append(command)

        class Result:
            pass

        result = Result()
        result.exit_code = self.exit_code
        result.output = self.output
        return result


def service_class() -> type[Any]:
    from assistant_api.container_builder.container_plugin.command_monitor_plugin import (
        CommandMonitorPluginService,
    )

    return CommandMonitorPluginService


def unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
