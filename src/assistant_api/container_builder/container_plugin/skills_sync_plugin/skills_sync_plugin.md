---
tags:
  - implementation
  - plugin
---

Этот сервис является реализацией [[../container_plugin.md|ContainerPluginService]].

Его задача — установить выбранные OpenCode artifact bundles в system-wide OpenCode config directory.

# Responsibility
Единая ответственность этого сервиса — безопасно установить OpenCode artifacts из внешнего repository в global OpenCode config container.

То есть он:
- принимает список имён artifact bundles;
- получает artifacts из `https://github.com/dimitree54/opencode-plugins.git`;
- запускает upstream `install_plugins_system.py`;
- устанавливает выбранные bundles в OpenCode global user config directory from `XDG_CONFIG_HOME/opencode`;
- устанавливает bundle `AGENTS.md`, `opencode.json`, agents и skills как system-wide OpenCode artifacts;
- fail fast вместо перезаписи существующих system-wide artifacts.

OpenCode global user config artifacts are stored under `~/.config/opencode` inside the container. This is different from OpenCode managed OS-wide config, which is an admin-controlled config-file mechanism and is not where this service stores agents, skills, or `AGENTS.md`.

# Interfaces
Публичный сервис этой реализации называется `SkillsSyncPluginService`.

```python
from assistant_api.container_builder.container_plugin.skills_sync_plugin import (
    SkillsSyncPluginService,
)

plugin = SkillsSyncPluginService(["yid-notes-assistant"])
```

## Init time
```python
class SkillsSyncPluginService:
    def __init__(
        self,
        plugin_names: list[str],
        repo_url: str = "https://github.com/dimitree54/opencode-plugins.git",
        repo_ref: str = "main",
    ) -> None:
        pass
```

## Runtime
Runtime-интерфейс не добавляет ничего нового, а наследуется от [[../container_plugin.md|ContainerPluginService]].

During `configure_container`, the service registers one startup task in `ContainerSpec.startup_tasks`.

The startup task is a typed `ContainerStartupTask` model from `assistant_api.models` with:
- `name: str`;
- `command: list[str]`.

The startup task installs selected artifacts through upstream `install_plugins_system.py` and must finish before long-running OpenCode processes start.

During `post_start`, the service must verify inside the container that the system-wide OpenCode config directory contains installed OpenCode artifacts.

# Requirements
- `plugin_names` must contain at least one plugin name.
- Duplicate plugin names must fail fast.
- Missing plugin names in the artifacts repository must fail fast.
- The default repository must be `https://github.com/dimitree54/opencode-plugins.git`.
- The default repository ref must be `main`.
- The service must not install OpenCode artifacts into `ContainerSpec.working_dir`.
- The service must not require `ContainerSpec.working_dir`.
- The service must use upstream `install_plugins_system.py` to install artifacts system-wide.
- The service must pass `--config-dir "$XDG_CONFIG_HOME/opencode"` to upstream `install_plugins_system.py`.
- Missing `XDG_CONFIG_HOME` must fail fast.
- Bundle `opencode.json` content must be handled by upstream `install_plugins_system.py`.
- Existing system-wide target artifacts must fail according to upstream `install_plugins_system.py` behavior.
- Selected bundle conflicts reported by the artifacts installer must fail fast.
- The service must register artifact installation as a startup task, so artifacts are installed before long-running OpenCode processes start.
- The service must fail fast if installed artifacts are missing after the startup task has completed.
- The service must not implement its own backup, replace, merge, overwrite, or idempotency behavior around upstream installer decisions.

## Sub-services
Не выделяются.
