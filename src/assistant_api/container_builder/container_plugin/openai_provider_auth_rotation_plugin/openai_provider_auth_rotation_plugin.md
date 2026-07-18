---
tags:
  - implementation
  - plugin
  - plan
---

Этот сервис является планируемой реализацией [[../container_plugin.md|ContainerPluginService]].

Его задача - non-interactive rotation of OpenCode OpenAI provider auth credentials before OpenCode starts.

# Responsibility
Единая ответственность этого сервиса - выбрать один working OpenCode `openai` provider credential from a configured pool of persisted auth files.

То есть он:
- принимает список full OpenCode `auth.json` files;
- выбирает candidate credentials in random order during container startup;
- проверяет каждый candidate реальным `opencode run` request;
- записывает первый working `openai` credential into active OpenCode auth state;
- сохраняет other active provider credentials in the active auth file;
- falls back to OpenAI API-token auth only when all candidate auth files fail;
- настраивает default OpenCode model for the started container;
- probes credentials with a cheap OpenAI model by default;
- не меняет выбранный credential after startup succeeds;
- не запускает browser login or OAuth flow;
- не открывает external auth port.

# Interfaces
Публичный сервис этой реализации называется `OpenAIProviderAuthRotationPluginService`.

```python
from assistant_api.container_builder.container_plugin.openai_provider_auth_rotation_plugin import (
    OpenAIProviderAuthRotationPluginService,
)

plugin = OpenAIProviderAuthRotationPluginService(
    candidate_auth_files=[
        "/secure/opencode-auth/account-a/auth.json",
        "/secure/opencode-auth/account-b/auth.json",
    ],
)
```

## Init time
```python
from collections.abc import Callable, Sequence
from pathlib import Path

class OpenAIProviderAuthRotationPluginService:
    def __init__(
        self,
        candidate_auth_files: Sequence[str | Path],
        fallback_api_token_env_var: str = "OPENAI_API_KEY",
        opencode_model: str = "openai/gpt-5.5",
        probe_model: str = "openai/gpt-5.4-mini",
        probe_variant: str = "low",
        probe_message: str = "hi",
        probe_expected_text: str | None = None,
        probe_timeout_seconds: int = 180,
        on_auth_alert: Callable[[str], None] | None = None,
    ) -> None:
        pass
```

`candidate_auth_files` must be a non-empty list of host paths to full OpenCode `auth.json` files. Each file must be readable by the launcher process, parse as a JSON object, and contain a valid `openai` auth record.

Candidate files are mounted read-only into the container. Auth JSON, OAuth tokens, and API keys must not be embedded into the Dockerfile, image layers, startup task command text, startup task logs, or exception messages.

`fallback_api_token_env_var` names the launcher environment variable containing the fallback OpenAI API token.

`opencode_model` configures the default OpenAI model after a credential is selected.

`probe_model`, `probe_variant`, and `probe_message` configure the real model probe. By default the probe uses `openai/gpt-5.4-mini`, low reasoning, and the message `hi`.

`probe_expected_text`, when set, is text that a successful real model probe must return. When it is not set, a successful probe requires exit code 0 and non-empty model output.

`probe_timeout_seconds` is the bounded timeout for each real model probe.

`on_auth_alert` is an optional host-side callback. After startup succeeds, when every candidate failed and the run continued on the fallback API token, `post_start` invokes the callback with a credential-free human-readable message. Candidate success does not invoke the callback. Callback failures must not fail container startup.

## Runtime
Runtime-интерфейс не добавляет ничего нового, а наследуется от [[../container_plugin.md|ContainerPluginService]].

During `configure_container`, the service must:
- mount every candidate auth file read-only into a private container path;
- require `fallback_api_token_env_var` to exist in the launcher environment and contain a non-empty value;
- add the fallback API token to `ContainerSpec.env` under `fallback_api_token_env_var`;
- register one startup task that completes before long-running OpenCode processes start.

During startup, the service must:
- build a random permutation of candidate auth files;
- try every candidate at most once during that startup;
- copy only the candidate's `openai` auth record into the active OpenCode auth file;
- preserve unrelated provider credentials already present in the active OpenCode auth file;
- write active OpenCode auth atomically and with owner-only permissions when the container filesystem supports chmod;
- preserve compatibility with [[../opencode_persistence_plugin/opencode_persistence_plugin.md|OpenCodePersistencePluginService]] auth symlink layout;
- configure `opencode_model` as the default OpenCode model before each probe;
- run a real `opencode run` probe with `probe_model`, `probe_variant`, and `probe_message`;
- accept a candidate only when the probe exits successfully and returns valid output.

If all candidate auth files fail, the service must install fallback API-token auth from `fallback_api_token_env_var` and validate it with the same real `opencode run` probe. A successful fallback must record a credential-free result marker so `post_start` can notify `on_auth_alert` when configured.

If no candidate and no fallback works, startup must fail fast.

After startup succeeds, the service must not rotate or mutate credentials again for the lifetime of that container. Recreating the container starts a new random selection.

# Requirements
- Provider id is fixed to `openai`.
- The default fallback token env var must be `OPENAI_API_KEY`.
- The default OpenCode model must be `openai/gpt-5.5`.
- The default probe model must be `openai/gpt-5.4-mini`.
- The default probe variant must be `low`.
- The default probe message must be `hi`.
- The default probe timeout must be 180 seconds per candidate.
- Candidate auth files must be full OpenCode `auth.json` objects.
- Candidate auth files must contain an `openai` auth record.
- Candidate auth files may contain other provider credentials, but this service must copy only the `openai` record from candidates.
- The active OpenCode auth file path must follow OpenCode data-home behavior.
- Existing unrelated active provider credentials must be preserved.
- Random candidate order is per container startup.
- A failed candidate must not be retried during the same startup.
- Fallback API-token auth may be attempted only after all candidate auth files fail.
- Missing or empty fallback API token must fail fast when fallback is needed.
- Missing, unreadable, invalid JSON, or malformed candidate auth files must fail fast before container startup.
- The service must not require browser interaction.
- The service must not call OpenCode OAuth endpoints.
- The service must not use or compose with `OpenAIProviderApiTokenPluginService` or `OpenAIProviderLoginPluginService` for the same container.
- The service must not enable OpenCode global config, skills, agents, or chat history persistence by itself.
- The service must not expose OpenAI credentials in logs, exception messages, Dockerfile contents, generated shell command text, or test failure messages.
- The service must not mutate host environment variables.
- The service must not configure Google Drive, inbox, outbox, local skills, or workspace mounts.
- The service must fail fast if the active OpenCode auth file cannot be parsed as a JSON object.
- The service must fail fast if `opencode_model` is not an OpenCode OpenAI model name.
- The service must fail fast if `probe_model` is not an OpenCode OpenAI model name.
- The service must fail fast if `probe_variant` is empty or contains surrounding whitespace.
- The service must fail fast if `probe_message` is empty or contains surrounding whitespace.
- The service must fail fast if provided `probe_expected_text` is empty or contains surrounding whitespace.
- The service must fail fast if `probe_timeout_seconds` is not a positive integer.

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
    OpenAIProviderAuthRotationPluginService(
        candidate_auth_files=[
            "/secure/opencode-auth/account-a/auth.json",
            "/secure/opencode-auth/account-b/auth.json",
        ],
        fallback_api_token_env_var="OPENAI_API_KEY",
    ),
]
```

The rotation plugin must be composed after persistence if persisted active auth is desired. It must run its startup task before OpenCode starts.

# Testing requirements
Contract tests must cover:
- public import and init signature;
- missing and empty candidate list failures;
- missing, unreadable, invalid JSON, and malformed candidate file failures;
- candidate files without `openai` auth failure;
- invalid `fallback_api_token_env_var`, `opencode_model`, `probe_model`, `probe_variant`, `probe_message`, `probe_expected_text`, and `probe_timeout_seconds` failures;
- random non-repeating candidate order within one startup;
- startup task copies only candidate `openai` auth;
- startup task preserves unrelated active provider credentials;
- startup task writes through OpenCode persistence auth symlink;
- startup task falls back to `OPENAI_API_KEY` only after all candidates fail;
- successful fallback notifies `on_auth_alert` from `post_start` without credentials;
- successful candidate selection does not notify `on_auth_alert`;
- startup task fails fast when all candidates and fallback fail;
- startup task command text, captured logs, Dockerfile commands, and exception messages do not include token or auth JSON content.

Live container tests must cover:
- generated container with real `opencode serve`;
- real candidate auth selection before OpenCode starts;
- one intentionally failing candidate followed by one working candidate;
- fallback to real `OPENAI_API_KEY`;
- persisted active auth survives container recreate;
- container recreate runs a new random selection;
- real `opencode run` answer proves the selected auth method works.

# Sub-services
Не выделяются.
