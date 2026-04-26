---
tags:
  - implementation
  - plugin
---

Этот сервис является реализацией [[../container_plugin.md|ContainerPluginService]].

Его задача — запустить `opencode web` внутри container и опубликовать его наружу на host port.

# Responsibility
Единая ответственность этого сервиса — настроить runtime command и port publishing для OpenCode Web.

То есть он:
- задаёт команду запуска `opencode web`;
- слушает внутри container на `0.0.0.0`;
- публикует container port на host port;
- использует рабочую директорию, уже заданную другими plugins;
- если рабочая директория не задана, использует `/workspace`;
- не включает persistence;
- не монтирует project directory.

# Interfaces
Публичный сервис этой реализации называется `OpenCodeWebServerPluginService`.

```python
from assistant_api.container_builder.container_plugin.opencode_web_server_plugin import OpenCodeWebServerPluginService

plugin = OpenCodeWebServerPluginService(host_port=4097)
```

## Init time
```python
class OpenCodeWebServerPluginService:
    def __init__(self, host_port: int = 4096, container_port: int = 4096) -> None:
        pass
```

## Runtime
Runtime-интерфейс не добавляет ничего нового, а наследуется от [[../container_plugin.md|ContainerPluginService]].

Команда запуска:

```bash
opencode web --hostname 0.0.0.0 --port <container_port>
```

# Requirements
- Running OpenCode Web must be optional.
- Exposing OpenCode Web externally is part of this service.
- The service must not configure persistence.
- The service must not mount host directories.
- If no working directory was set by previous plugins, the service must use `/workspace`.
- The service must not add auth/password logic.

## Sub-services
Не выделяются.
