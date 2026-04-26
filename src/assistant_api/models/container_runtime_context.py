from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .command_exec_result import CommandExecResult


@dataclass(slots=True)
class ContainerRuntimeContext:
    docker_client: Any
    container: Any
    state: dict[str, object]

    def exec(self, command: list[str]) -> CommandExecResult:
        result = self.container.exec_run(command)
        output = result.output
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return CommandExecResult(exit_code=result.exit_code, output=output)
