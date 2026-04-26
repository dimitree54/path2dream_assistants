---
tags:
  - implementation
  - plugin
---

Этот сервис является реализацией [[../container_plugin.md|ContainerPluginService]].

Его задача — открыть отдельную страницу для login в OpenAI-compatible provider, которым пользуется OpenCode.

# Responsibility
Единая ответственность этого сервиса — предоставить web endpoint для provider auth и простой status endpoint.

То есть он:
- запускает отдельный auth web server;
- публикует его наружу на отдельный host port;
- показывает browser login page;
- проверяет provider auth через OpenCode server API;
- проводит OAuth flow через OpenCode provider endpoints;
- отдаёт JSON status для health checks и внешних gate-сервисов;
- не запускает OpenCode server сам;
- не включает OpenCode persistence;
- не монтирует project directory.

# Interfaces
Публичный сервис этой реализации называется `OpenAIProviderLoginPluginService`.

```python
from assistant_api.container_builder.container_plugin.openai_provider_login_plugin import (
    OpenAIProviderLoginPluginService,
)

plugin = OpenAIProviderLoginPluginService(host_port=4101)
```

## Init time
```python
class OpenAIProviderLoginPluginService:
    def __init__(
        self,
        host_port: int = 4101,
        container_port: int = 4101,
        provider_id: str = "openai",
        opencode_api_url: str = "http://127.0.0.1:4096",
    ) -> None:
        pass
```

## Runtime
Runtime-интерфейс не добавляет ничего нового, а наследуется от [[../container_plugin.md|ContainerPluginService]].

Published endpoints:
- `GET /login`;
- `GET /status`.

OpenCode provider endpoints used by this service:
- `GET /provider`;
- `GET /provider/auth`;
- `POST /provider/{provider_id}/oauth/authorize`;
- `POST /provider/{provider_id}/oauth/callback`.

# Requirements
- The default host port must be `4101`.
- The default container port must be `4101`.
- The default provider id must be `openai`.
- `/login` must allow the user to start or complete provider login in a browser.
- `/status` must return JSON with at least `authValid`, `state`, `message`, and `providerName`.
- Provider auth must be checked against the configured OpenCode API URL.
- Missing OpenCode server availability must be reported as an explicit status state.
- Missing OAuth methods for the configured provider must be reported as an explicit status state.
- Successful auth must be visible through `/status`.
- The service must fail fast on invalid init-time configuration.
- The service must not configure OpenCode persistence.
- The service must not mount project directories.

## Sub-services
Не выделяются.
