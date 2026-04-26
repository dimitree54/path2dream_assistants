---
tags:
  - implementation
  - plugin
---

Этот сервис является реализацией [[../container_plugin.md|ContainerPluginService]].

Его задача — сделать OpenCode config, auth и session state переживающими rebuild и recreate container.

# Responsibility
Единая ответственность этого сервиса — подключить named Docker volumes и env vars, которые задают стабильные OpenCode state paths.

То есть он:
- задаёт `HOME`;
- задаёт `XDG_CONFIG_HOME`;
- задаёт `XDG_DATA_HOME`;
- подключает named volume для OpenCode config;
- подключает named volume для OpenCode data/session/auth state;
- не запускает OpenCode;
- не открывает порт;
- не монтирует project directory.

# Interfaces
Публичный сервис этой реализации называется `OpenCodePersistencePluginService`.

```python
from assistant_api.container_builder.container_plugin.opencode_persistence_plugin import OpenCodePersistencePluginService

plugin = OpenCodePersistencePluginService()
```

## Init time
```python
class OpenCodePersistencePluginService:
    def __init__(
        self,
        config_volume: str = "notes_assistant_api_opencode_config",
        data_volume: str = "notes_assistant_api_opencode_data",
        home: PurePosixPath = PurePosixPath("/tmp/opencode-home"),
    ) -> None:
        pass
```

## Runtime
Runtime-интерфейс не добавляет ничего нового, а наследуется от [[../container_plugin.md|ContainerPluginService]].

# Requirements
- Without this plugin, OpenCode Web may still run, but config/auth/session state is not guaranteed to survive container recreation.
- The default `HOME` must be `/tmp/opencode-home`.
- The default config volume must mount to `/tmp/opencode-home/.config/opencode`.
- The default data volume must mount to `/tmp/opencode-home/.local/share/opencode`.
- The service must not start OpenCode Web.
- The service must not expose ports.
- The service must not mount host project directories.

## Sub-services
Не выделяются.
