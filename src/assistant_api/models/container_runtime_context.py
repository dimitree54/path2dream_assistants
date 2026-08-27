from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .command_exec_result import CommandExecResult
from .container_execution_identity import ContainerExecutionIdentity


@dataclass(slots=True)
class ContainerRuntimeContext:
    docker_client: Any
    container: Any
    state: dict[str, object]
    execution_identity: ContainerExecutionIdentity | None = None

    def exec(self, command: list[str]) -> CommandExecResult:
        exec_command = command
        kwargs: dict[str, str] = {}
        if self.execution_identity is not None:
            exec_command = self.execution_identity.wrap_command(command)
            kwargs["user"] = self.execution_identity.docker_user
        result = self.container.exec_run(exec_command, **kwargs)
        output = result.output
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return CommandExecResult(exit_code=result.exit_code, output=output)
