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
- запускает отдельный auth web server внутри container;
- публикует container-side auth web server наружу на отдельный host port;
- показывает production-ready Pet Project Cofounder branded browser login page для headless/device auth flow;
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
        opencode_model: str = "openai/gpt-5.5",
        host: str | None = None,
    ) -> None:
        pass
```

The auth flow must run fully inside the container. The host/external auth port is configured through `host_port`.

The optional Docker host bind address is configured through `host`. When `host` is not provided, Docker default bind behavior must be preserved. When `host` is provided, the auth port must bind only to that host address.

The container/internal auth port is `auth_container_port` when provided; otherwise the service may choose its own internal port. Caller-provided environment variables must not be required for either auth host/external port or OpenCode API port.

The OpenAI model OpenCode should use by default is configured through `opencode_model`. When not provided, the service uses `openai/gpt-5.5`.

## Runtime
Runtime-интерфейс не добавляет ничего нового, а наследуется от [[../container_plugin.md|ContainerPluginService]].

During `configure_container`, the service must read standard OpenCode runtime state from `ContainerSpec.state`.

Auth service обращается к OpenCode только внутри container по `http://127.0.0.1:<api_container_port>`, where `api_container_port` comes from OpenCode runtime metadata.

If OpenCode runtime state is missing, configuration must fail fast.

During `post_start`, the service must wait until its container-local `/status` endpoint is reachable and reports a non-error state. If `/status` reports `error` or `unavailable`, the hook must fail fast.

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
- The service must accept optional Docker host bind address through init-time configuration as `host`.
- Invalid `host` bind values must fail fast.
- The service must run the OpenAI provider auth web server inside the container and expose it only through Docker port publishing.
- The service must not start host-side auth servers, host-side HTTP listeners, host-side background threads, or host-side auth flow processes.
- The service must not require the launcher Python process to stay alive after `build_and_run()` for published auth endpoints to remain available.
- The service must not require `OPENAI_AUTH_PORT` from environment variables.
- The service must not require `OPENCODE_API_PORT` from environment variables.
- The service must read OpenCode API container port from standard OpenCode runtime state.
- Missing OpenCode runtime state must lead to fail fast.
- `/login` must allow the user to start or complete OpenAI provider login in a browser.
- `/login` must render a production-ready Pet Project Cofounder branded page with proper document title, responsive UI, clear authorization instructions, and primary action to open the OpenAI authorization page.
- `/login` must render the OpenAI device-code section as a heading `OpenAI device code`, the hint `Copy this code.`, a code-only field, and a small icon-only copy button inside that code field with accessible label `Copy device code`.
- `/login` waiting status hint must tell the user to use the button above to open OpenAI authorization, enter the device code, and finish the flow.
- `/login` must not render a bottom note about extra confirmation clicks after the OpenAI page accepts the code.
- `/login` must use the repository asset `assets/petprojectcofounder_logo_small.PNG` for Pet Project Cofounder branding, and this asset must be tracked through Git LFS.
- `/login` must use the shared repository style asset `../assets/petprojectcofounder_login_page.css`; this CSS is the single source of truth for both Google Drive and OpenAI provider login page styling.
- `/login` must not expose the auth flow as only a raw/simple link page.
- After headless/device authorization starts, the login page must automatically check completion and update to an authorization success message when OpenAI provider auth becomes valid.
- The primary login flow must not require the user to click a separate completion or "return to bot" link after entering the OpenAI code.
- Primary production login flow is OpenCode headless/device OAuth flow.
- Browser redirect OAuth flow is not the production contract for remote containers.
- `/status` must return JSON with at least `authValid`, `state`, `message`, and `providerName`.
- `/status.state` must be one of `unavailable`, `unauthenticated`, `authenticated`, or `error`.
- On status check, it should use OpenCode server API to check that the OpenAI provider is available, and OpenCode auth storage to check that real OpenAI credentials are present.
- `/status.authValid=true` must require valid OpenCode `openai` auth credentials in `~/.local/share/opencode/auth.json` or equivalent OpenCode auth content. OpenCode `/provider.connected` alone must not be treated as successful auth.
- Provider auth must be checked against the local OpenCode API URL derived from OpenCode runtime metadata.
- The service must accept the OpenAI model name for OpenCode through init-time configuration as `opencode_model`.
- The default `opencode_model` must be `openai/gpt-5.5`.
- After successful OpenAI provider auth, OpenCode must be configured to use `opencode_model` as its default OpenAI model.
- After successful OpenAI provider auth, OpenCode API calls made without an explicit model must use `opencode_model`.
- Missing OpenCode server availability at startup must lead to fail fast.
- Missing OpenAI provider in OpenCode `/provider` response must lead to fail fast.
- Missing headless OAuth method for OpenAI in OpenCode `/provider/auth` response must lead to fail fast.
- The service must fail fast if its container-local `/status` endpoint does not become healthy after the managed auth process starts.
- The service must not fall back to redirect OAuth or API key auth when headless OAuth is unavailable.
- Successful auth must be visible through `/status`.
- When `/status` reports successful OpenAI auth, `/login` must keep the success status card visible and hide the full device-code card and OpenAI authorization button.
- Manual live integration coverage for this service must include a real `opencode serve` container run composed with `OpenCodePersistencePluginService` default volumes and must verify that OpenAI auth remains valid after container recreation.
- The service must fail fast on invalid init-time configuration.
- The service must not configure OpenCode persistence.
- The service must not overwrite the OpenCode long-running process.

## Sub-services
Не выделяются.
