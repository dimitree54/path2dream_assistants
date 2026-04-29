---
tags:
  - implementation
  - plugin
---

Этот сервис является реализацией [[../container_plugin.md|ContainerPluginService]].

Его задача — сделать так, чтобы mounted directory внутри рабочей директории контейнера имела то же имя, что и локальная host directory.

# Responsibility
Единая ответственность этого сервиса — преобразовать mount layout после того, как предыдущий plugin уже описал mount source.

То есть он:
- требует `MountMetadata` из стандартного mount-aware state;
- переносит bind mount target на `/workspace/mounted-source`;
- задаёт рабочую директорию container как `/workspace/workdir`;
- после старта container создаёт symlink `/workspace/workdir/<host_mount_basename> -> /workspace/mounted-source`;
- не создаёт mount source сам;
- не запускает OpenCode;
- не включает persistence.

# Interfaces
Публичный сервис этой реализации называется `SyncMountDirNamePluginService`.

```python
from assistant_api.container_builder.container_plugin.sync_mount_dir_name_plugin import SyncMountDirNamePluginService

plugin = SyncMountDirNamePluginService()
```

## Init time
```python
class SyncMountDirNamePluginService:
    def __init__(
        self,
        working_dir: PurePosixPath = PurePosixPath("/workspace/workdir"),
        mounted_source: PurePosixPath = PurePosixPath("/workspace/mounted-source"),
    ) -> None:
        pass
```

## Runtime
Runtime-интерфейс не добавляет ничего нового, а наследуется от [[../container_plugin.md|ContainerPluginService]].

During `post_start`, the service must create the same-name symlink and verify inside the container that the symlink points to the mounted source and that the mounted source is accessible.

# Requirements
- The service must fail fast if no previous plugin provided mount metadata.
- The default working directory must be `/workspace/workdir`.
- The default raw mounted source target must be `/workspace/mounted-source`.
- The same-name entry inside working dir must be a symlink to the mounted source.
- The service must fail fast if the same-name symlink cannot be created or verified.
- The service must not expose ports.
- The service must not configure OpenCode persistence.
- The service must not start OpenCode Web.

## Sub-services
Не выделяются.
