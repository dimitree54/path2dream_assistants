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
from pathlib import PurePosixPath
from assistant_api.models import LocalSkillPostInstallCommand

class LocalSkillsPluginService:
    def __init__(
        self,
        source_path: str | Path,
        *,
        post_install_commands: list[LocalSkillPostInstallCommand] | None = None,
    ) -> None:
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

`post_install_commands` optionally declares exact commands that run after the
artifacts are installed into `$XDG_CONFIG_HOME/opencode` and before long-running
OpenCode processes start.

```python
LocalSkillPostInstallCommand(
    name="install-remotion-renderer-deps",
    working_dir=PurePosixPath("skills/clip-editor/scripts/remotion_artifact_renderer"),
    command=["npm", "ci"],
)
```

Each post-install command must have a non-empty name, a relative POSIX
`working_dir` under `$XDG_CONFIG_HOME/opencode`, and a non-empty exact argv
`command`. Absolute paths, empty path segments, `.` and `..` path segments are
invalid. The service must not interpret command strings through a shell unless
the caller explicitly chooses a shell as the argv executable.

## Runtime
Runtime-интерфейс не добавляет ничего нового, а наследуется от [[../container_plugin.md|ContainerPluginService]].

During `configure_container`, the service registers one read-only bind mount for `source_path` and startup tasks in `ContainerSpec.startup_tasks`.

The startup task copies supported artifacts into `$XDG_CONFIG_HOME/opencode` and must finish before long-running OpenCode processes start.

Post-install startup tasks run after the artifact-copy startup task, inside the
installed `$XDG_CONFIG_HOME/opencode/<working_dir>` directory. If any command
fails, container startup must fail fast with the command task output. Post-install
commands must operate on the installed config copy, not on the read-only source
mount.

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
- The service must register post-install commands as startup tasks after artifact installation and before long-running OpenCode processes start.
- Post-install commands must run in `$XDG_CONFIG_HOME/opencode/<working_dir>`.
- Post-install commands must not run against the read-only source mount.
- Invalid post-install command name, `working_dir`, or argv must fail fast before container startup.
- The service must fail fast if installed artifacts are missing or unreadable after the startup task has completed.
- The source mount inside the container must be read-only and outside `/workspace`.

## Sub-services
Не выделяются.
