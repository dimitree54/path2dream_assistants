---
tags:
  - implementation
  - plugin
  - plan
---

Этот сервис является планируемой реализацией [[../container_plugin.md|ContainerPluginService]].

Его задача - non-interactive auth для OpenCode OpenAI provider через OpenAI API token.

# Research basis
OpenCode supports provider API-key credentials through `/connect` and stores them in the OpenCode auth file under the data directory.

For the OpenAI provider, the relevant credential is the provider id `openai` with an API-key auth record:

```json
{
  "openai": {
    "type": "api",
    "key": "<OpenAI API token>"
  }
}
```

OpenCode also supports loading OpenAI credentials from `OPENAI_API_KEY` and from provider config options, but a persisted `openai` OAuth credential can override direct API-key paths in some OpenCode versions. This service therefore targets the OpenCode auth file directly and must refuse ambiguous existing OpenAI auth state by default.

# Responsibility
Единая ответственность этого сервиса - установить OpenAI API token как OpenCode provider auth credential before OpenCode starts.

То есть он:
- получает OpenAI API token from a configured secret environment variable;
- записывает OpenCode auth credential for provider id `openai`;
- сохраняет другие existing provider credentials in the same auth file;
- настраивает default OpenCode model for OpenAI after auth is installed;
- validates that OpenCode sees the OpenAI provider as API-key authenticated;
- fail fast вместо silent fallback to OAuth, stale credentials, missing token, or partial auth setup;
- не запускает OpenCode;
- не запускает browser login or OAuth flow;
- не открывает external auth port.

# Interfaces
Публичный сервис этой реализации называется `OpenAIProviderApiTokenPluginService`.

```python
from assistant_api.container_builder.container_plugin.openai_provider_api_token_plugin import (
    OpenAIProviderApiTokenPluginService,
)

plugin = OpenAIProviderApiTokenPluginService(
    api_token_env_var="OPENAI_API_KEY",
)
```

## Init time
```python
class OpenAIProviderApiTokenPluginService:
    def __init__(
        self,
        api_token_env_var: str = "OPENAI_API_KEY",
        opencode_model: str = "openai/gpt-5.5",
        replace_existing: bool = False,
    ) -> None:
        pass
```

`api_token_env_var` names the launcher environment variable containing the OpenAI API token. The value is read by the launcher process and passed to the container at runtime. The token must not be embedded into the Dockerfile, image layers, startup task command text, or startup task logs.

`opencode_model` configures the default OpenAI model that OpenCode should use after API-key auth is installed.

`replace_existing` controls conflicts with existing `openai` auth:
- when `False`, any existing `openai` auth record that is not the same API token must fail fast;
- when `True`, the startup task may replace the existing `openai` auth record with the configured API token.

## Runtime
Runtime-интерфейс не добавляет ничего нового, а наследуется от [[../container_plugin.md|ContainerPluginService]].

During `configure_container`, the service must:
- require `api_token_env_var` to exist in the launcher environment and contain a non-empty value;
- add the token to `ContainerSpec.env` under `api_token_env_var`;
- register one `ContainerStartupTask` that installs the `openai` API credential before long-running OpenCode processes start;
- register one `ContainerStartupTask` that configures `opencode_model` as the default OpenAI model, or include that configuration in the same startup task if the task remains clear and testable.

The auth file path must follow OpenCode data-home behavior:
- when `XDG_DATA_HOME` is set inside the container, use `$XDG_DATA_HOME/opencode/auth.json`;
- otherwise, when `HOME` is set inside the container, use `$HOME/.local/share/opencode/auth.json`;
- if neither is set, fail fast.

During `post_start`, the service must verify inside the container that:
- the OpenCode auth file exists and is readable;
- the `openai` auth record exists;
- the `openai` auth record has `type: "api"`;
- the stored key exactly matches the configured token value without printing the token;
- the container-local OpenCode API can list OpenAI as an available connected provider when OpenCode runtime metadata is available.

# Requirements
- Provider id is fixed to `openai`.
- The default token env var must be `OPENAI_API_KEY`.
- The default OpenCode model must be `openai/gpt-5.5`.
- The service must fail fast when the configured token env var is missing or empty.
- The service must fail fast when `opencode_model` is not an OpenCode OpenAI model name.
- The service must not require browser interaction.
- The service must not call OpenCode OAuth endpoints.
- The service must not use `OpenAIProviderLoginPluginService`.
- The service must not fall back to OAuth, ChatGPT subscription auth, provider config `apiKey`, or unauthenticated OpenCode state.
- The service must not silently ignore an existing non-API `openai` auth record.
- The service must not overwrite existing non-OpenAI provider credentials in `auth.json`.
- The service must write `auth.json` atomically and with owner-only file permissions when the container filesystem supports chmod.
- The service must preserve compatibility with `OpenCodePersistencePluginService`; when auth persistence is enabled, the installed API-token auth must survive container restart, rebuild, and recreate through that service's auth volume.
- The service must not enable OpenCode global config, skills, agents, or chat history persistence by itself.
- The service must not expose the API token in logs, exception messages, Dockerfile contents, or generated shell command text.
- The service must not mutate host environment variables.
- The service must not configure Google Drive, inbox, outbox, local skills, or workspace mounts.
- The service must fail fast if its startup task cannot parse an existing auth file as JSON.
- The service must fail fast if the existing auth file root is not a JSON object.
- The service must fail fast if the OpenCode provider list cannot be queried during `post_start` when OpenCode runtime metadata is present.
- The service may run without OpenCode runtime metadata during `configure_container`, but provider API validation in `post_start` requires metadata from `OpenCodeServerPluginService` or `OpenCodeWebServerPluginService`.

# Composition
Recommended composition for headless server use:

```python
plugins = [
    OpenCodePersistencePluginService(
        config_volume="my_instance_opencode_config",
        data_volume="my_instance_opencode_data",
        persist_auth=True,
        persist_chat_history=True,
        persist_opencode_artifacts=False,
        persist_skills=False,
        persist_agents=False,
    ),
    OpenCodeServerPluginService(host_port=4096),
    OpenAIProviderApiTokenPluginService(api_token_env_var="OPENAI_API_KEY"),
]
```

The API-token plugin must be composed after persistence if persisted auth is desired. It must run its startup task before OpenCode starts.

`OpenAIProviderLoginPluginService` and `OpenAIProviderApiTokenPluginService` are mutually exclusive for the same container unless a future documented contract defines explicit precedence.

# Testing requirements
Contract tests must cover:
- public import and init signature;
- missing and empty token env var failures;
- invalid `opencode_model` failures;
- startup task writes `{"openai": {"type": "api", "key": ...}}` to the OpenCode auth path;
- startup task preserves unrelated provider credentials;
- startup task rejects invalid existing JSON;
- startup task rejects existing OAuth `openai` auth when `replace_existing=False`;
- startup task replaces existing OpenAI auth only when `replace_existing=True`;
- startup task does not include the token in command text or captured logs;
- post-start health check succeeds only for matching API-key auth;
- post-start health check fails for missing auth, OAuth auth, mismatched token, or unavailable OpenCode API when runtime metadata exists.

Live container tests must cover:
- generated container with real `opencode serve`;
- real startup task order before OpenCode starts;
- real OpenCode auth file path in the generated container;
- real `opencode` provider listing behavior with the installed API-token credential;
- composition with `OpenCodePersistencePluginService` proving the API-token auth survives container recreate.

Tests that perform a real OpenAI model request may be manual only when they require a human-controlled or paid third-party OpenAI account state. Docker runtime behavior, auth file installation, and OpenCode provider loading must be non-manual `live_container` tests.

# Sub-services
Не выделяются.
