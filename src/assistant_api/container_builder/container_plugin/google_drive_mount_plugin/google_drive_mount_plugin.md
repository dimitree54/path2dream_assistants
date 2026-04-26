---
tags:
  - implementation
  - plugin
---

Этот сервис является реализацией [[../container_plugin.md|ContainerPluginService]].

Его задача — подключить Google Drive как mount source вместо локальной директории.

# Responsibility
Единая ответственность этого сервиса — авторизовать Google Drive с минимальным доступом к видимой app-owned папке и смонтировать эту папку внутрь container через `rclone mount`.

То есть он:
- запускает отдельный Google Drive auth web server;
- публикует его наружу на отдельный host port;
- показывает browser login page;
- создаёт или переиспользует обычную видимую папку приложения в Google Drive;
- создаёт rclone config после Google OAuth;
- запускает `rclone mount`;
- монтирует эту Google Drive папку в тот же container path, который использует local mount;
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
        drive_api_base_url: str = "https://www.googleapis.com/drive/v3",
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

# Google Drive access model
Сервис использует intermediate Google Drive access level:
- mounted storage is a normal user-visible folder in `My Drive`;
- the app must be able to read and write files it creates inside this folder;
- the user must be able to see and manage this folder and its files through Google Drive UI;
- the app must not receive full-drive access.

The mounted folder is app-owned from the OAuth perspective. User-visible Drive operations such as viewing, downloading, renaming, moving, or deleting files are part of the supported model. Automatic access to arbitrary files that the user manually adds through Google Drive UI is not part of this contract unless those files are explicitly opened, selected, shared, or otherwise authorized for this app under the same minimal-scope access model.

# Requirements
- The default host port must be `4102`.
- The default container port must be `4102`.
- The default mount target must be `/workspace/project`.
- The default rclone remote name must be `gdrive`.
- The service must require `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, and `GOOGLE_DRIVE_MOUNT_FOLDER_NAME`.
- OAuth authorize and token endpoints must be configurable at init time and default to Google OAuth endpoints.
- Custom OAuth endpoints must be sufficient for the full OAuth flow, so local OAuth-compatible providers can be used without live Google OAuth.
- Google Drive API base URL must be configurable at init time and default to `https://www.googleapis.com/drive/v3`.
- The service must request the minimum Google Drive OAuth scopes that support the visible app-owned folder use case.
- The default Google Drive OAuth scope must be `https://www.googleapis.com/auth/drive.file`.
- The service must not request full-drive scopes such as `https://www.googleapis.com/auth/drive`, `https://www.googleapis.com/auth/drive.readonly`, `https://www.googleapis.com/auth/drive.metadata`, or `https://www.googleapis.com/auth/drive.metadata.readonly`.
- The service must not use `https://www.googleapis.com/auth/drive.appdata` or `https://www.googleapis.com/auth/drive.appfolder` as the mounted file storage scope because the mounted files must be visible to the user in Google Drive UI.
- The service must create or reuse a dedicated app-owned folder in the user's `My Drive` using folder name from `GOOGLE_DRIVE_MOUNT_FOLDER_NAME`.
- The mounted rclone remote root must be restricted to this dedicated app-owned folder, not the user's whole `My Drive`.
- The service must fail fast if the dedicated app-owned folder cannot be created, found, authorized, or mounted.
- `/login` must return an HTML login page that lets the user authorize Google Drive in a browser.
- `GET /oauth/callback` must complete the OAuth redirect flow.
- `/logout` must stop the active Google Drive mount, remove stored Google Drive auth and rclone config for this container state, and return the service to unauthenticated state.
- `/status` must return JSON with at least `authValid`, `mounted`, `state`, and `message`.
- `/status.state` must be one of `unauthenticated`, `authenticating`, `authenticated`, `mounting`, `mounted`, or `error`.
- The service must create rclone config from Google OAuth credentials before mounting.
- Google Drive must be mounted with `rclone mount`.
- `/status` must report `mounted=true` only after `rclone mount` starts successfully and the container path is verified as a mountpoint.
- The service must request Docker runtime capabilities required for FUSE, including `/dev/fuse`, `cap_add`, and security options.
- The service must record `MountMetadata` so mount-aware plugins can use it.
- Google Drive `MountMetadata` must identify the remote mount source using `remote_name`, use the Google Drive folder name as its display basename, and must not imply a local host directory.
- The service must fail fast instead of silently using a local directory when Google Drive is not mounted.
- The service must not expose or depend on local host directory mounts.
- The service must not configure OpenCode persistence.

## Sub-services
Не выделяются.
