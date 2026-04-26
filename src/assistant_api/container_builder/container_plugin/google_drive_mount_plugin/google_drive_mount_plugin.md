---
tags:
  - implementation
  - plugin
---

Этот сервис является реализацией [[../container_plugin.md|ContainerPluginService]].

Его задача — подключить Google Drive как mount source вместо локальной директории.

# Responsibility
Единая ответственность этого сервиса — авторизовать Google Drive и смонтировать его внутрь container через `rclone mount`.

То есть он:
- запускает отдельный Google Drive auth web server;
- публикует его наружу на отдельный host port;
- показывает browser login page;
- создаёт rclone config после Google OAuth;
- запускает `rclone mount`;
- монтирует Google Drive в тот же container path, который использует local mount;
- сохраняет `MountMetadata` в стандартный mount-aware state;
- отдаёт JSON status для проверки login/mount state;
- не запускает OpenCode;
- не включает OpenCode persistence.

# Interfaces
Публичный сервис этой реализации называется `GoogleDriveMountPluginService`.

```python
from pathlib import PurePosixPath

from assistant_api.container_builder.container_plugin.google_drive_mount_plugin import (
    GoogleDriveMountPluginService,
)

plugin = GoogleDriveMountPluginService(host_port=4102)
```

## Init time
```python
class GoogleDriveMountPluginService:
    def __init__(
        self,
        host_port: int = 4102,
        container_port: int = 4102,
        container_path: PurePosixPath = PurePosixPath("/workspace/project"),
        remote_name: str = "gdrive",
        mode: str = "rw",
        oauth_authorize_url: str = "https://accounts.google.com/o/oauth2/v2/auth",
        oauth_token_url: str = "https://oauth2.googleapis.com/token",
    ) -> None:
        pass
```

## Runtime
Runtime-интерфейс не добавляет ничего нового, а наследуется от [[../container_plugin.md|ContainerPluginService]].

Published endpoints:
- `GET /login`;
- `GET /oauth/callback`;
- `GET /logout`;
- `GET /status`.

# Requirements
- The default host port must be `4102`.
- The default container port must be `4102`.
- The default mount target must be `/workspace/project`.
- The default rclone remote name must be `gdrive`.
- The service must require `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`.
- OAuth authorize and token endpoints must be configurable at init time and default to Google OAuth endpoints.
- Custom OAuth endpoints must be sufficient for the full OAuth flow, so local OAuth-compatible providers can be used without live Google OAuth.
- `/login` must return an HTML login page that lets the user authorize Google Drive in a browser.
- `GET /oauth/callback` must complete the OAuth redirect flow.
- `/logout` must remove stored Google Drive auth for this container state.
- `/status` must return JSON with at least `authValid`, `mounted`, `state`, and `message`.
- `/status.state` must be one of `unauthenticated`, `authenticating`, `authenticated`, `mounting`, `mounted`, or `error`.
- The service must create rclone config from Google OAuth credentials before mounting.
- Google Drive must be mounted with `rclone mount`.
- `/status` must report `mounted=true` only after `rclone mount` starts successfully and the container path is verified as a mountpoint.
- The service must request Docker runtime capabilities required for FUSE, including `/dev/fuse`, `cap_add`, and security options.
- The service must record `MountMetadata` so mount-aware plugins can use it.
- Google Drive `MountMetadata` must identify the remote mount source using `remote_name` and must not imply a local host directory.
- The service must fail fast instead of silently using a local directory when Google Drive is not mounted.
- The service must not expose or depend on local host directory mounts.
- The service must not configure OpenCode persistence.

## Sub-services
Не выделяются.
