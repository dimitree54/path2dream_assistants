---
tags:
  - implementation
  - plugin
---

Этот сервис является реализацией [[../container_plugin.md|ContainerPluginService]].

Его задача — запустить headless `opencode serve` внутри container и опубликовать его наружу на host port.

# Responsibility
Единая ответственность этого сервиса — настроить runtime command и port publishing для OpenCode Server без Web UI.

То есть он:
- задаёт команду запуска `opencode serve`;
- слушает внутри container на `0.0.0.0`;
- публикует container port на host port;
- использует рабочую директорию, уже заданную другими plugins;
- если рабочая директория не задана, использует `/workspace`;
- сохраняет OpenCode runtime metadata в стандартный OpenCode runtime state;
- не включает persistence;
- не монтирует project directory.

# Interfaces
Публичный сервис этой реализации называется `OpenCodeServerPluginService`.

```python
from assistant_api.container_builder.container_plugin.opencode_server_plugin import OpenCodeServerPluginService

plugin = OpenCodeServerPluginService(host_port=4097)
```

## Init time
```python
class OpenCodeServerPluginService:
    def __init__(
        self,
        host_port: int = 4096,
        container_port: int = 4096,
        wait_for_mount: bool = False,
        host: str | None = None,
    ) -> None:
        pass
```

## Runtime
Runtime-интерфейс не добавляет ничего нового, а наследуется от [[../container_plugin.md|ContainerPluginService]].

During `configure_container`, the service must finalize `ContainerSpec.working_dir` before registering the OpenCode process and must record OpenCode runtime metadata in `ContainerSpec.state` using the standard OpenCode runtime state key.

The recorded metadata must contain:
- `working_dir` — final `ContainerSpec.working_dir`, from which OpenCode is launched;
- `api_container_port` — container-local port passed to `opencode serve --port` and used by other plugins for local OpenCode API calls.

During `post_start`, the service must wait until the container-local `GET /global/health` endpoint reports healthy on `api_container_port`. Each health request attempt must be bounded so a single stuck HTTP request cannot block startup indefinitely. The health probe must inspect a captured response body, not a long-lived `wget | grep` pipeline.

The configured working directory must be mounted before OpenCode starts. If `wait_for_mount=False`, the service must fail fast when the working directory is not a mountpoint. If `wait_for_mount=True`, the service must log a warning and wait indefinitely, periodically checking until the working directory becomes a mountpoint, before starting OpenCode and before running the health probe.

Команда запуска:

```bash
opencode serve --hostname 0.0.0.0 --port <container_port>
```

# Requirements
- Running OpenCode Server must be optional.
- Exposing OpenCode Server externally is part of this service.
- `host_port` must be configured through init-time configuration and must be used as the host/external port for OpenCode Server.
- `container_port` must be the container-local OpenCode Server API port.
- `wait_for_mount` must default to `False`.
- `host` must be optional init-time configuration for Docker host bind address; when omitted, Docker default bind behavior must be preserved.
- If `host` is provided, the published OpenCode Server port must bind only to that host address.
- Invalid `host` bind values must fail fast.
- The service must not configure persistence.
- The service must not mount host directories.
- If no working directory was set by previous plugins, the service must use `/workspace`.
- The service must record the final working directory and API container port in standard OpenCode runtime state.
- If `wait_for_mount=False`, the service must fail fast when the final working directory is not mounted.
- If `wait_for_mount=True`, the service must wait indefinitely for the final working directory to become mounted.
- The service must fail fast if the OpenCode Server health endpoint does not become healthy inside the container.
- The service must not add auth/password logic.
- The service must not start or open Web UI.

## Sub-services
Не выделяются.
