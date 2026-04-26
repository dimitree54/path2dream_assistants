---
tags:
  - implementation
  - plugin
---

Этот сервис является реализацией [[../container_plugin.md|ContainerPluginService]].

Его задача — установить выбранные OpenCode artifact bundles в директорию, из которой запускается OpenCode.

# Responsibility
Единая ответственность этого сервиса — безопасно перенести OpenCode artifacts из внешнего repository в target directory container.

То есть он:
- принимает список имён artifact bundles;
- получает artifacts из `https://github.com/dimitree54/opencode-plugins.git`;
- устанавливает выбранные bundles в target directory;
- проверяет, что OpenCode artifacts ещё не находятся в target directory;
- fail fast вместо перезаписи существующих artifacts;
- uses the state to understand where to store the artifacts (it should be stored to the same dir, where opencode launched. Parent dir of where working dir is mounted)

# Interfaces
Публичный сервис этой реализации называется `OpenCodeArtifactsPluginService`.

```python
from pathlib import PurePosixPath

from assistant_api.container_builder.container_plugin.skills_sync_plugin import (
    OpenCodeArtifactsPluginService,
)

plugin = OpenCodeArtifactsPluginService(["yid-notes-assistant"])
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

# Requirements
- `plugin_names` must contain at least one plugin name.
- Duplicate plugin names must fail fast.
- Missing plugin names in the artifacts repository must fail fast.
- The default repository must be `https://github.com/dimitree54/opencode-plugins.git`.
- The default repository ref must be `main`.
- The service must fail before installation if target `AGENTS.md` already exists.
- The service must fail before installation if target `.opencode/agents` already exists.
- The service must fail before installation if target `.opencode/skills` already exists.
- Selected bundle conflicts reported by the artifacts installer must fail fast.
- The service must install artifacts before long-running OpenCode processes start.
- The service must not back up and replace existing target artifacts.

## Sub-services
Не выделяются.
