---
tags:
  - implementation
  - plugin
---

Этот сервис является реализацией [[../container_plugin.md|ContainerPluginService]].

Его задача — открыть отдельную страницу для login в OpenAI provider, которым пользуется OpenCode.

# Responsibility
Единая ответственность этого сервиса — предоставить web endpoint для provider auth и простой status endpoint.

То есть он:
- запускает отдельный auth web server;
- публикует его наружу на отдельный container/host port;
- показывает browser login page для headless/device auth flow;
- проверяет provider auth через локальный OpenCode server API, порт которого берёт из стандартного OpenCode runtime state;
- проводит OAuth flow через OpenCode provider endpoints;
- отдаёт JSON status для health checks и внешних gate-сервисов;
- не запускает отдельный container;
- не зависит от внешнего host address.

# Interfaces
Публичный сервис этой реализации называется `OpenAIProviderLoginPluginService`.

```python
from assistant_api.container_builder.container_plugin.openai_provider_login_plugin import (
    OpenAIProviderLoginPluginService,
)

plugin = OpenAIProviderLoginPluginService(host_port=4323)
```

## Init time
```python
class OpenAIProviderLoginPluginService:
    def __init__(
        self,
        host_port: int,
        auth_container_port: int | None = None,
    ) -> None:
        pass
```

The host/external auth port is configured through `host_port`.

The container/internal auth port is `auth_container_port` when provided; otherwise the service may choose its own internal port. Caller-provided environment variables must not be required for either auth host/external port or OpenCode API port.

## Runtime
Runtime-интерфейс не добавляет ничего нового, а наследуется от [[../container_plugin.md|ContainerPluginService]].

During `configure_container`, the service must read standard OpenCode runtime state from `ContainerSpec.state`.

Auth service обращается к OpenCode только внутри container по `http://127.0.0.1:<api_container_port>`, where `api_container_port` comes from OpenCode runtime metadata.

If OpenCode runtime state is missing, configuration must fail fast.

Published endpoints:
- `GET /login`;
- `GET /status`.

OpenCode provider endpoints used by this service:
- `GET /global/health`;
- `GET /provider`;
- `GET /provider/auth`;
- `POST /provider/{provider_id}/oauth/authorize`;
- `POST /provider/{provider_id}/oauth/callback`.

# Requirements
- Provider id is fixed to `openai`.
- The service must accept auth host/external port through init-time configuration as `host_port`.
- The service must not require `OPENAI_AUTH_PORT` from environment variables.
- The service must not require `OPENCODE_API_PORT` from environment variables.
- The service must read OpenCode API container port from standard OpenCode runtime state.
- Missing OpenCode runtime state must lead to fail fast.
- `/login` must allow the user to start or complete OpenAI provider login in a browser.
- Primary production login flow is OpenCode headless/device OAuth flow.
- Browser redirect OAuth flow is not the production contract for remote containers.
- `/status` must return JSON with at least `authValid`, `state`, `message`, and `providerName`.
- `/status.state` must be one of `unavailable`, `unauthenticated`, `authenticated`, or `error`.
- On status check, it should use OpenCode server API to check the OpenAI provider status.
- Provider auth must be checked against the local OpenCode API URL derived from OpenCode runtime metadata.
- Missing OpenCode server availability at startup must lead to fail fast.
- Missing OpenAI provider in OpenCode `/provider` response must lead to fail fast.
- Missing headless OAuth method for OpenAI in OpenCode `/provider/auth` response must lead to fail fast.
- The service must not fall back to redirect OAuth or API key auth when headless OAuth is unavailable.
- Successful auth must be visible through `/status`.
- The service must fail fast on invalid init-time configuration.
- The service must not configure OpenCode persistence.
- The service must not overwrite the OpenCode long-running process.

## Sub-services
Не выделяются.
