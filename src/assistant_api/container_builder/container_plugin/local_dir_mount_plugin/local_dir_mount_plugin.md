---
tags:
  - implementation
  - plugin
---

Этот сервис является реализацией [[../container_plugin.md|ContainerPluginService]].

Его задача — подключить локальную директорию host machine внутрь Docker container.

# Responsibility
Единая ответственность этого сервиса — описать bind mount локальной директории.

То есть он:
- принимает локальный путь;
- добавляет bind mount в `ContainerSpec`;
- сохраняет `MountMetadata` в стандартный mount-aware state;
- не запускает OpenCode;
- не меняет exposed ports;
- не включает persistence.

# Interfaces
Публичный сервис этой реализации называется `LocalDirMountPluginService`.

```python
from assistant_api.container_builder.container_plugin.local_dir_mount_plugin import LocalDirMountPluginService

plugin = LocalDirMountPluginService(".")
```

## Init time
```python
class LocalDirMountPluginService:
    def __init__(
        self,
        host_path: str | Path,
        container_path: PurePosixPath = PurePosixPath("/workspace/project"),
        mode: str = "rw",
    ) -> None:
        pass
```

## Runtime
Runtime-интерфейс не добавляет ничего нового, а наследуется от [[../container_plugin.md|ContainerPluginService]].

During `post_start`, the service must verify inside the container that `container_path` exists, is readable, and is writable when the mount mode is not `ro`.

# Requirements
- By default host path must be mounted into `/workspace/project`.
- The service must record `MountMetadata` so mount-aware plugins can use it.
- The service must fail fast if the mounted directory is not usable inside the container.
- The service must not imply that OpenCode runs from the mounted directory.
- The service must not expose any port.
- The service must not configure OpenCode persistence.

## Sub-services
Не выделяются.
