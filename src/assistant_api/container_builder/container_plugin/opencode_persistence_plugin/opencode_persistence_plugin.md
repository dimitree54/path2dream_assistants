---
tags:
  - implementation
  - plugin
---

Этот сервис является реализацией [[../container_plugin.md|ContainerPluginService]].

Его задача — сделать выбранные категории OpenCode state переживающими restart, rebuild и recreate container.

# Responsibility
Единая ответственность этого сервиса — подключить Docker-backed storage и env vars, которые задают стабильные OpenCode state paths для включённых категорий persistence.

OpenCode global user config artifacts are stored under `~/.config/opencode` inside the container. This is different from OpenCode managed OS-wide config, which is an admin-controlled config-file mechanism and is not where this service stores agents, skills, or `AGENTS.md`.

OpenCode provider auth created through `/connect` is stored under `~/.local/share/opencode/auth.json` inside the container.

OpenCode chat/session history is stored under `~/.local/share/opencode` inside the container. The current OpenCode storage includes `opencode.db` with SQLite sidecar files and may include legacy/migration storage under `storage/`.

То есть он:
- задаёт `HOME`;
- задаёт `XDG_CONFIG_HOME`;
- задаёт `XDG_DATA_HOME`;
- задаёт `OPENCODE_DB` when chat/session history is persisted without mounting the whole OpenCode data directory;
- optionally persists OpenCode auth state;
- optionally persists OpenCode chat/session history;
- optionally persists OpenCode global config/rule artifacts;
- optionally persists OpenCode skills;
- optionally persists OpenCode agents;
- не подключает persistent state для disabled persistence categories;
- не запускает OpenCode;
- не открывает порт;
- не монтирует project workspace directory.

# Interfaces
Публичный сервис этой реализации называется `OpenCodePersistencePluginService`.

```python
from assistant_api.container_builder.container_plugin.opencode_persistence_plugin import OpenCodePersistencePluginService

plugin = OpenCodePersistencePluginService(
    config_volume="my_instance_opencode_config",
    data_volume="my_instance_opencode_data",
    chat_history_host_dir="/projects/p1/opencode-history",
)
```

## Init time
```python
from pathlib import Path, PurePosixPath

class OpenCodePersistencePluginService:
    def __init__(
        self,
        config_volume: str,
        data_volume: str,
        home: PurePosixPath = PurePosixPath("/root"),
        *,
        persist_auth: bool = True,
        persist_chat_history: bool = True,
        persist_opencode_artifacts: bool = True,
        persist_skills: bool = True,
        persist_agents: bool = True,
        chat_history_host_dir: str | Path | None = None,
    ) -> None:
        pass
```

Volume name parameters (`config_volume`, `data_volume`) are required and have no defaults. Callers must pass instance-specific names to prevent credential leakage when multiple instances share the same Docker host. Init-time `persist_*` flags define which OpenCode state categories are persisted. Defaults persist every supported category, preserving the previous all-state persistence behavior.

`chat_history_host_dir` optionally stores only OpenCode chat/session history in a caller-owned host directory. The path must already exist and must be a directory. The caller owns creating, archiving, and deleting this directory.

The categories are:
- `persist_auth`: OpenCode provider auth state, including `~/.local/share/opencode/auth.json`.
- `persist_chat_history`: OpenCode chat/session history under `~/.local/share/opencode`, including `opencode.db`, SQLite sidecar files, and legacy/migration `storage/` data.
- `persist_opencode_artifacts`: OpenCode global config/rule artifacts under `~/.config/opencode`, such as `opencode.json`, `opencode.jsonc`, `tui.json`, `AGENTS.md`, commands, modes, plugins, tools, and themes. This category excludes `skills/` and `agents/`, because they are controlled by dedicated flags.
- `persist_skills`: OpenCode global skills under `~/.config/opencode/skills`.
- `persist_agents`: OpenCode global agents under `~/.config/opencode/agents`.

The concrete Docker mount layout is an implementation detail. The public contract is that enabled categories survive container restart, rebuild, and recreate, while disabled categories are not backed by persistent Docker state by this service.

When `chat_history_host_dir` is set, chat/session history is backed by a bind mount of that host directory instead of the history named volume. The container path is the same isolated history path used by the granular volume backend, and `OPENCODE_DB` points to that directory's `opencode.db`.

## Runtime
Runtime-интерфейс не добавляет ничего нового, а наследуется от [[../container_plugin.md|ContainerPluginService]].

During `post_start`, the service must verify inside the container that the enabled OpenCode persistence targets exist and are writable.

# Requirements
- Without this plugin, OpenCode Web may still run, but OpenCode state is not guaranteed to survive container restart, rebuild, or recreate.
- The default `HOME` must be `/root`.
- The default `XDG_CONFIG_HOME` must be `/root/.config`.
- The default `XDG_DATA_HOME` must be `/root/.local/share`.
- When chat/session history is persisted without the whole OpenCode data directory, the service must set `OPENCODE_DB` to the persisted OpenCode SQLite database path.
- The service must support configuring auth persistence independently through `persist_auth`.
- The service must support configuring chat/session history persistence independently through `persist_chat_history`.
- The service must support configuring global OpenCode config/rule artifact persistence independently through `persist_opencode_artifacts`.
- The service must support configuring skills persistence independently through `persist_skills`.
- The service must support configuring agents persistence independently through `persist_agents`.
- The service must support a caller-provided `chat_history_host_dir` for chat/session history persistence.
- With default init values, auth, chat/session history, OpenCode artifacts, skills, and agents must all survive container restart, rebuild, and recreate.
- When `persist_auth` is `False`, this service must not persist `~/.local/share/opencode/auth.json`.
- When `persist_chat_history` is `False`, this service must not persist OpenCode chat/session history under `~/.local/share/opencode`.
- When `chat_history_host_dir` is set, `persist_chat_history` must be `True`.
- When `chat_history_host_dir` is set, the host path must already exist and must be a directory.
- When `chat_history_host_dir` is set, the service must bind mount that host directory to `/tmp/notes-assistant/opencode-persistence/history`.
- When `chat_history_host_dir` is set, the service must not use the `{data_volume}_history` named volume.
- When `chat_history_host_dir` is set, the service must not use the full OpenCode data-directory persistence shortcut, even if every `persist_*` flag is enabled.
- When `chat_history_host_dir` is set, `auth.json` and other non-history OpenCode state must not be written into the host history directory.
- When `persist_opencode_artifacts` is `False`, this service must not persist OpenCode global config/rule artifacts under `~/.config/opencode`, except categories explicitly enabled by `persist_skills` or `persist_agents`.
- When `persist_skills` is `False`, this service must not persist `~/.config/opencode/skills`.
- When `persist_agents` is `False`, this service must not persist `~/.config/opencode/agents`.
- The service must fail fast if any enabled OpenCode persistence target is not writable inside the container.
- The service must not start OpenCode Web.
- The service must not expose ports.
- The service must not mount host project workspace directories.

## Sub-services
Не выделяются.
