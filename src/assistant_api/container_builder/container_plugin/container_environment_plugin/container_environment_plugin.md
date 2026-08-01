---
tags:
  - implementation
  - plugin
---

Этот сервис является реализацией [[../container_plugin.md|ContainerPluginService]].

Его задача — явно передать заданные environment variables в runtime container.

# Responsibility
Единая ответственность этого сервиса — добавить explicit environment mapping в `ContainerSpec.env`.

То есть он:
- принимает и копирует environment mapping во время инициализации;
- проверяет names и string values;
- добавляет exact values только в runtime container environment;
- не читает host environment и не логирует values;
- не меняет image и не запускает runtime behavior.

# Interfaces
Публичный сервис этой реализации называется `ContainerEnvironmentPluginService`.

```python
from assistant_api.container_builder.container_plugin.container_environment_plugin import (
    ContainerEnvironmentPluginService,
)

plugin = ContainerEnvironmentPluginService(
    environment={"OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS": "600000"}
)
```

## Init time
```python
class ContainerEnvironmentPluginService:
    def __init__(self, environment: Mapping[str, str]) -> None:
        pass
```

Environment variable names must match `[A-Za-z_][A-Za-z0-9_]*`. Every value must be a string. Empty string values are valid.

The service must validate and defensively copy the complete mapping during initialization. Later caller mutations must not affect the service.

## Runtime
Runtime-интерфейс не добавляет ничего нового, а наследуется от [[../container_plugin.md|ContainerPluginService]].

During `configure_container`, the service must add every configured name and exact value to `ContainerSpec.env`.

An existing identical value is accepted idempotently. If any existing value differs, configuration must fail before adding any environment values. Error messages must identify the conflicting name without including either value.

# Requirements
- `ImageSpec.env` и остальная image configuration не должны изменяться.
- Environment values должны поступать только из init-time mapping, а не из host environment.
- Invalid mapping, name, or non-string value must fail fast during initialization.
- Conflicting `ContainerSpec.env` values must never be silently overwritten.
- Environment values must never appear in logs or error messages.

## Sub-services
Не выделяются.
