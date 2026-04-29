---
tags:
  - implementation
  - plugin
  - plan
---

Этот сервис является реализацией [[../container_plugin.md|ContainerPluginService]].

Его задача — сделать Google Drive auth и rclone state переживающими restart, rebuild и recreate container.

# Responsibility
Единая ответственность этого сервиса — подключить named Docker volumes и env vars, которые задают стабильные rclone state paths для [[../google_drive_mount_plugin/google_drive_mount_plugin.md|GoogleDriveMountPluginService]].

То есть он:
- задаёт `RCLONE_CONFIG`;
- задаёт `RCLONE_CACHE_DIR`;
- подключает named volume для rclone config/auth state;
- подключает named volume для rclone VFS/cache state;
- не запускает Google Drive auth web server;
- не выполняет Google OAuth;
- не запускает `rclone mount`;
- не открывает порт;
- не монтирует project directory;
- не запускает OpenCode;
- не включает OpenCode persistence.

# Interfaces
Публичный сервис этой реализации называется `GoogleDrivePersistencePluginService`.

```python
from pathlib import PurePosixPath

from assistant_api.container_builder.container_plugin.google_drive_persistence_plugin import (
    GoogleDrivePersistencePluginService,
)

plugin = GoogleDrivePersistencePluginService()
```

## Init time
```python
class GoogleDrivePersistencePluginService:
    def __init__(
        self,
        config_volume: str = "notes_assistant_api_google_drive_config",
        cache_volume: str = "notes_assistant_api_google_drive_cache",
        config_dir: PurePosixPath = PurePosixPath("/tmp/google-drive-persistence/rclone-config"),
        cache_dir: PurePosixPath = PurePosixPath("/tmp/google-drive-persistence/rclone-cache"),
    ) -> None:
        pass
```

## Runtime
Runtime-интерфейс не добавляет ничего нового, а наследуется от [[../container_plugin.md|ContainerPluginService]].

During `post_start`, the service must verify inside the container that the configured rclone config and cache directories exist and are writable.

# Persistence model
The persisted rclone config contains Google OAuth tokens, including refresh tokens when Google returns them. This volume is secret-bearing state.

When this plugin is composed with `GoogleDriveMountPluginService`:
- first browser login writes rclone config to the persisted `RCLONE_CONFIG` path;
- container restart reuses the same rclone config volume;
- container rebuild/recreate reuses the same rclone config volume;
- Google Drive mount can be restored without repeating browser login while the persisted OAuth token remains valid;
- `/logout` removes the persisted rclone config for the configured remote and returns the mount flow to unauthenticated state.

The persisted rclone cache volume stores rclone VFS/cache files. It must not be treated as the source of truth for mounted files: Google Drive remains the source of truth. Abrupt container termination before rclone uploads cached writes can still lose writes that were not flushed/uploaded.

# Requirements
- Without this plugin, Google Drive mount may still work, but Google Drive auth state is not guaranteed to survive container restart, rebuild, or recreate.
- The default config volume must mount to `/tmp/google-drive-persistence/rclone-config`.
- The default cache volume must mount to `/tmp/google-drive-persistence/rclone-cache`.
- The service must set `RCLONE_CONFIG` to `/tmp/google-drive-persistence/rclone-config/rclone.conf` by default.
- The service must set `RCLONE_CACHE_DIR` to `/tmp/google-drive-persistence/rclone-cache` by default.
- The service must fail fast if the configured rclone config or cache directory is not writable inside the container.
- The service must not set `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, or `XDG_CACHE_HOME`, so it does not conflict with other persistence plugins.
- The service must not start Google Drive auth web server.
- The service must not perform Google OAuth.
- The service must not create, delete, or validate Google OAuth tokens.
- The service must not run `rclone mount`.
- The service must not expose ports.
- The service must not mount host project directories.
- The service must not configure OpenCode persistence.
- `GoogleDriveMountPluginService` must fail fast if this plugin is composed but the persisted rclone config path cannot be used.
- `GoogleDriveMountPluginService` must restore mount from persisted rclone config without browser login when valid persisted auth exists.
- `GoogleDriveMountPluginService` must still expose `/login`, `/logout`, and `/status` when this plugin is composed.
- `/status.authValid` must report `true` when persisted rclone auth is valid, even before a new browser login in the recreated container.
- `/logout` must clear persisted Google Drive auth for the configured rclone remote.

## Sub-services
Не выделяются.
