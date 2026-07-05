---
tags:
  - implementation
  - plugin
---

Этот сервис является реализацией [[../container_plugin.md|ContainerPluginService]].

Его задача — установить private OpenCode artifacts из host-local directory в global OpenCode config directory внутри container.

# Responsibility
Единая ответственность этого сервиса — безопасно скопировать локальные private OpenCode skills и связанные artifacts в `$XDG_CONFIG_HOME/opencode` до запуска OpenCode.

То есть он:
- принимает host-local directory with private OpenCode artifacts;
- монтирует этот directory read-only внутрь container вне `/workspace`;
- устанавливает artifacts в OpenCode global user config directory from `XDG_CONFIG_HOME/opencode`;
- устанавливает skills, optional agents, optional `AGENTS.md`, and optional `opencode.json`;
- fail fast вместо merge, replace, backup, overwrite или silent partial install;
- не кладёт reusable/private skill definitions в mounted workspace.

OpenCode global user config artifacts are stored under `~/.config/opencode` inside the container. This is different from OpenCode managed OS-wide config, which is an admin-controlled config-file mechanism and is not where this service stores agents, skills, or `AGENTS.md`.

# Interfaces
Публичный сервис этой реализации называется `LocalSkillsPluginService`.

```python
from assistant_api.container_builder.container_plugin.local_skills_plugin import (
    LocalSkillsPluginService,
)

plugin = LocalSkillsPluginService("/host/private/opencode-artifacts")
```

## Init time
```python
from pathlib import Path

class LocalSkillsPluginService:
    def __init__(self, source_path: str | Path) -> None:
        pass
```

`source_path` points to one local OpenCode artifact root.

Supported artifact layout:
- required: `.opencode/skills/<skill_name>/SKILL.md` for at least one skill;
- optional: `AGENTS.md`;
- optional: `opencode.json`;
- optional: `.opencode/agents/*.md`.

Direct OS metadata files under `.opencode/skills` are ignored only for `.DS_Store`,
`Thumbs.db`, and `desktop.ini`. Any other non-directory entry under
`.opencode/skills` must fail fast.

The service records the selected artifact set during init. Changing `source_path`
after service construction is outside this contract.

## Runtime
Runtime-интерфейс не добавляет ничего нового, а наследуется от [[../container_plugin.md|ContainerPluginService]].

During `configure_container`, the service registers one read-only bind mount for `source_path` and one startup task in `ContainerSpec.startup_tasks`.

The startup task copies supported artifacts into `$XDG_CONFIG_HOME/opencode` and must finish before long-running OpenCode processes start.

During `post_start`, the service must verify inside the container that selected local artifacts are present and readable in `$XDG_CONFIG_HOME/opencode`.

# Requirements
- `source_path` must exist on the host.
- `source_path` must be a readable directory.
- The source must contain at least one skill under `.opencode/skills/<skill_name>/SKILL.md`.
- The service must not install OpenCode artifacts into `ContainerSpec.working_dir`.
- The service must not require `ContainerSpec.working_dir`.
- The service must not set `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, or `XDG_CACHE_HOME`.
- Missing `XDG_CONFIG_HOME` inside the container must fail fast.
- The target config directory must be `$XDG_CONFIG_HOME/opencode`.
- Existing target `AGENTS.md`, `opencode.json`, selected agent files, or selected skill directories must fail fast before copying.
- The service must not merge, replace, backup, overwrite, or partially install artifacts.
- The service must register artifact installation as a startup task, so artifacts are installed before long-running OpenCode processes start.
- The service must fail fast if installed artifacts are missing or unreadable after the startup task has completed.
- The source mount inside the container must be read-only and outside `/workspace`.

## Sub-services
Не выделяются.
