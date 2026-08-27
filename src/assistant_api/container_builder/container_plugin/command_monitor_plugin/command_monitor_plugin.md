---
tags:
  - implementation
  - plugin
---

Этот сервис является реализацией [[../container_plugin.md|ContainerPluginService]].

Его задача — мониторинг зафейлившихся shell-команд, которые OpenCode agent выполняет внутри container, для последующего анализа недостающих в image инструментов и пакетов.

# Research basis
OpenCode автоматически загружает JS/TS plugin files из `$XDG_CONFIG_HOME/opencode/{plugin,plugins}/*.{ts,js}`.

Plugin hook `tool.execute.after` для built-in инструмента `bash` получает:
- `input.args.command` — выполненную команду;
- `output.metadata.exit` — exit code (`127` = command not found, `126` = not executable, `null` = timeout/abort);
- `output.output` — объединённый stdout+stderr.

При ненулевом exit code инструмент `bash` возвращает результат нормально (не бросает исключение), поэтому hook срабатывает на каждую зафейлившуюся команду.

# Responsibility
Единая ответственность этого сервиса — установить в container OpenCode plugin, который аккумулирует записи обо всех зафейлившихся командах инструмента `bash` в JSONL log file на persistent named volume или explicit caller-owned bind directory.

То есть он:
- встраивает JS-исходник OpenCode plugin в container image;
- устанавливает plugin file в OpenCode config directory через startup task до запуска OpenCode;
- монтирует выбранный persistent source для log directory, чтобы накопленный log переживал container restart, rebuild и recreate;
- проверяет после запуска container, что plugin file установлен и log directory доступна на запись;
- не анализирует накопленный log;
- не публикует ports;
- не запускает managed processes;
- не логирует успешные команды;
- не перехватывает non-bash tools.

# Log contract
Log file: `/tmp/notes-assistant/command-monitor/failed-commands.jsonl`.

OpenCode plugin дописывает одну JSON-строку на каждый вызов инструмента `bash` с `metadata.exit !== 0` (включая `null` — timeout/abort). Поля записи:

```json
{
  "timestamp": "ISO-8601 момент записи",
  "sessionID": "OpenCode session id",
  "callID": "tool call id",
  "command": "выполненная команда",
  "description": "описание команды от agent",
  "workdir": "рабочая директория команды или null",
  "exit": 127,
  "output_tail": "хвост объединённого stdout+stderr, до 4000 символов"
}
```

Ошибки записи в log пробрасываются (fail fast), silent degradation мониторинга запрещена.

Log rotation не выполняется.

Чтение накопленного log — через `docker exec` или `RunningContainerCommandRunnerService`; отдельный инструмент анализа не входит в контракт.

# Interfaces
Публичный сервис этой реализации называется `CommandMonitorPluginService`.

```python
from assistant_api.container_builder.container_plugin.command_monitor_plugin import (
    CommandMonitorPluginService,
)

plugin = CommandMonitorPluginService(log_volume="my_instance_command_monitor_logs")
non_root_plugin = CommandMonitorPluginService(log_host_dir="/srv/my-instance/command-monitor")
```

## Init time
```python
class CommandMonitorPluginService:
    def __init__(
        self,
        log_volume: str | None = None,
        *,
        log_host_dir: str | Path | None = None,
    ) -> None:
        pass
```

Exactly one persistence source is required. `log_volume` is a named Docker volume. `log_host_dir` is an explicit caller-owned bind directory.

## Runtime
Runtime-интерфейс не добавляет ничего нового, а наследуется от [[../container_plugin.md|ContainerPluginService]].

During `configure_image`, the service must:
- declare `python3` through `ImageSpec.apk_packages` (used to embed the plugin source file);
- embed the OpenCode plugin source into the image at a private path outside the OpenCode config directory.

During `configure_container`, the service must:
- mount the selected persistence source at `/tmp/notes-assistant/command-monitor`;
- register one `ContainerStartupTask` that installs the plugin file into `$XDG_CONFIG_HOME/opencode/plugins/notes-assistant-command-monitor.js` and prepares the log directory;
- fail fast in the startup task when `XDG_CONFIG_HOME` is not set inside the container.

The installed plugin file is owned by this service (namespaced file name), so the startup task overwrites it on every container start to propagate plugin source updates.

During `post_start`, the service must verify inside the container that:
- the plugin file exists and is readable in the OpenCode config directory;
- the log directory exists and is writable (probe write/delete).

# Requirements
- Сервис должен мониторить только built-in OpenCode инструмент `bash`.
- Сервис должен логировать каждую команду инструмента `bash` с `metadata.exit !== 0`, включая `null`.
- Сервис не должен логировать команды с `metadata.exit === 0`.
- Log record должен содержать поля `timestamp`, `sessionID`, `callID`, `command`, `description`, `workdir`, `exit`, `output_tail`.
- `output_tail` должен быть ограничен 4000 символами.
- Log file должен лежать на configured persistent source и переживать container restart, rebuild и recreate.
- Exactly one of `log_volume` and `log_host_dir` must be configured and invalid values must fail fast.
- When container execution identity is selected, `log_host_dir` is required and named-volume persistence must fail fast because a root-created named volume does not satisfy the ownership contract.
- A selected writable `log_host_dir` must exist, have the exact execution UID/GID, and be writable by that identity before container work. The service must not create, chown, chmod, or recursively normalize it.
- Startup task должен fail fast, если `XDG_CONFIG_HOME` не задан.
- Сервис должен быть скомпонован после `OpenCodePersistencePluginService`, когда persistence используется: startup task должен выполняться после persistence layout, чтобы plugin file попал в итоговую OpenCode config directory.
- Сервис не должен записывать plugin file в image-level OpenCode config directory, потому что persistence volumes затеняют её при mount.
- Сервис не должен модифицировать `opencode.json`.
- Сервис не ловит фейлы tool-вызовов, брошенные исключением до выполнения команды (известное ограничение `tool.execute.after`).

# Composition
Recommended composition:

```python
plugins = [
    OpenCodePersistencePluginService(
        config_volume="my_instance_opencode_config",
        data_volume="my_instance_opencode_data",
    ),
    CommandMonitorPluginService(log_volume="my_instance_command_monitor_logs"),
    OpenCodeServerPluginService(host_port=4096),
]
```

# Testing requirements
Contract tests must cover:
- public import and init signature;
- persistence-source validation failures;
- `ImageSpec` receives `python3` and plugin source embedding commands;
- `ContainerSpec` receives the selected volume or bind and the startup task;
- startup task requires `XDG_CONFIG_HOME` and installs the namespaced plugin file;
- plugin JS source contains the `tool.execute.after` hook, the `bash` tool filter, and the documented log file path;
- post-start health check failure raises.

Live container tests must cover:
- image builds with the embedded plugin source and the startup task installs the plugin file into the real OpenCode config directory;
- real `opencode serve` stays healthy with the installed plugin loaded;
- end-to-end: a real OpenCode agent run executes a missing binary and the JSONL log receives a record with the command and `exit` 127.
- execution-identity mode writes through the caller-owned bind with the selected UID/GID and umask-derived mode.

# Sub-services
Не выделяются.
