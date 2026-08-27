---
tags:
  - interface
---

Этот сервис выполняет bounded commands внутри уже запущенного container.

# Responsibility
Единая ответственность этого сервиса — запустить одну команду внутри `RunningContainer`, дождаться её завершения в заданный timeout и вернуть typed result.

То есть он:
- проверяет, что container всё ещё запущен перед стартом команды;
- запускает переданный argument vector без shell interpolation;
- применяет optional working directory внутри container;
- захватывает combined command output;
- возвращает exit code без скрытия nonzero status;
- прерывает команду при timeout;
- скрывает Docker SDK details от публичного command execution contract.

# Interfaces
Публичный сервис этого модуля называется `RunningContainerCommandRunnerService`.

```python
from pathlib import PurePosixPath

from assistant_api.container_builder import RunningContainerCommandRunnerService
from assistant_api.models import RunningContainer

running_container: RunningContainer = ...
runner = RunningContainerCommandRunnerService(running_container)

result = runner.run_command(
    ["opencode", "run", prompt],
    working_dir=PurePosixPath("/workspace"),
    timeout_seconds=1800,
)
```

## Init time
```python
class RunningContainerCommandRunnerService:
    def __init__(self, running_container: RunningContainer) -> None:
        pass
```

## Runtime
```python
class RunningContainerCommandRunnerService:
    def run_command(
        self,
        command: list[str],
        *,
        working_dir: PurePosixPath | None = None,
        timeout_seconds: int,
    ) -> CommandExecResult:
        pass
```

# Requirements
- `command` must be a non-empty `list[str]`.
- `timeout_seconds` must be positive.
- The container status must be reloaded and must be `running` before command start.
- If `working_dir` is provided, it must be an absolute `PurePosixPath`.
- If Docker rejects the requested `working_dir`, the service must fail fast with `ContainerCommandError`.
- The command vector must be passed to Docker exec without shell interpolation by default.
- When `RunningContainer.container_spec` contains an execution identity, Docker exec must use its exact UID/GID and apply its umask through an argv-safe wrapper before the requested command starts.
- Execution identity must also apply to timeout TERM/KILL helper execs; ordinary command execution and cleanup must not regain root.
- Stdout and stderr must be captured as combined text in `CommandExecResult.output`.
- Nonzero command exits must return `CommandExecResult` with the original exit code.
- Docker exec startup failures must raise `ContainerCommandError`.
- Timeout must terminate the command and raise `ContainerCommandTimeoutError`.
- Timeout errors must expose an output tail suitable for developer alerts.
- Raw Docker SDK objects must not be required for public command execution.

## Sub-services
Не выделяются.
