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
- запускает отдельный Google Drive auth web server внутри container;
- публикует container-side auth web server наружу на отдельный host port;
- показывает production-ready browser login page с брендингом приложения;
- создаёт или переиспользует обычную видимую папку приложения в Google Drive;
- создаёт rclone config после Google OAuth;
- запускает `rclone mount`;
- монтирует эту Google Drive папку в container path, вычисленный из OpenCode runtime state или явно переданный через init;
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

plugin = GoogleDriveMountPluginService(host_port=4322, drive_folder_name="notes")
```

## Init time
```python
class GoogleDriveMountPluginService:
    def __init__(
        self,
        host_port: int,
        drive_folder_name: str,
        container_path: PurePosixPath | None = None,
        auth_container_port: int | None = None,
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

When `container_path` is not provided at init time, the service must read standard OpenCode runtime state from `ContainerSpec.state` and derive the mount target as:

```python
opencode_runtime.working_dir / drive_folder_name
```

If OpenCode runtime state is missing and no explicit `container_path` was provided, configuration must fail fast.

The Google Drive auth flow must run fully inside the container. The host/external auth port is `host_port`. The container/internal auth port is `auth_container_port` when provided; otherwise the service may choose its own internal port. Caller-provided environment variables must not be required for either host/external port or Google Drive folder name.

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

# Google OAuth credentials
Google OAuth Web client credentials must be provided through `GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON`.

The variable must contain the full Google Console OAuth client JSON with a top-level `web` object. The service must read `client_id` and `client_secret` from that JSON and fail fast if the variable is missing, is not valid JSON, does not describe a Web client, or does not contain both required fields.

# Requirements
- The service must not hardcode a default Google Drive auth host/external port.
- The service must accept Google Drive auth host/external port through init-time configuration as `host_port`.
- The service must run the Google Drive auth web server inside the container and expose it only through Docker port publishing.
- The service must not start host-side auth servers, host-side HTTP listeners, host-side background threads, or host-side auth flow processes.
- The service must not require the launcher Python process to stay alive after `build_and_run()` for published auth endpoints to remain available.
- The service must not require `GOOGLE_DRIVE_AUTH_PORT` from environment variables.
- The service must accept Google Drive folder name through init-time configuration as `drive_folder_name`.
- The service must not require `GOOGLE_DRIVE_MOUNT_FOLDER_NAME` from environment variables.
- If `container_path` is omitted, the service must derive mount target from standard OpenCode runtime state as `working_dir / drive_folder_name`.
- If `container_path` is omitted and OpenCode runtime state is missing, the service must fail fast.
- If `container_path` is provided, the service must use it as the mount target without requiring OpenCode runtime state.
- The default rclone remote name must be `gdrive`.
- The service must require `GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON`.
- OAuth authorize and token endpoints must be configurable at init time and default to Google OAuth endpoints.
- Custom OAuth endpoints must be sufficient for the full OAuth flow, so local OAuth-compatible providers can be used without live Google OAuth.
- Google Drive API base URL must be configurable at init time and default to `https://www.googleapis.com/drive/v3`.
- The service must request the minimum Google Drive OAuth scopes that support the visible app-owned folder use case.
- The default Google Drive OAuth scope must be `https://www.googleapis.com/auth/drive.file`.
- The service must not request full-drive scopes such as `https://www.googleapis.com/auth/drive`, `https://www.googleapis.com/auth/drive.readonly`, `https://www.googleapis.com/auth/drive.metadata`, or `https://www.googleapis.com/auth/drive.metadata.readonly`.
- The service must not use `https://www.googleapis.com/auth/drive.appdata` or `https://www.googleapis.com/auth/drive.appfolder` as the mounted file storage scope because the mounted files must be visible to the user in Google Drive UI.
- The service must create or reuse a dedicated app-owned folder in the user's `My Drive` using `drive_folder_name`.
- The mounted rclone remote root must be restricted to this dedicated app-owned folder, not the user's whole `My Drive`.
- The service must fail fast if the dedicated app-owned folder cannot be created, found, authorized, or mounted.
- `/login` must return a production-ready HTML login page that lets the user authorize Google Drive in a browser.
- When Google Drive is already mounted, `/login` must return the mounted success page instead of restarting OAuth.
- `/login` must not be a plain text page or a bare authorization link; it must provide a proper title, polished visual layout, clear Google Drive authorization copy, and a primary call-to-action.
- `/login` must use the repository asset `assets/petprojectcofounder_logo_small.PNG` for Pet Project Cofounder branding, and this asset must be tracked through Git LFS.
- `/login` must use the shared repository style asset `../assets/petprojectcofounder_login_page.css`; this CSS is the single source of truth for both Google Drive and OpenAI provider login page styling.
- `GET /oauth/callback` must complete the OAuth redirect flow.
- After a successful `GET /oauth/callback`, the service must return a production-ready Pet Project Cofounder branded HTML success page using the shared repository style asset. The page must explain that Google Drive was mounted successfully and that the user can proceed to using the Assistant.
- The mounted success page must provide a visible logout button that calls `/logout`.
- `/logout` must stop the active Google Drive mount, remove stored Google Drive auth and rclone config for this container state, return the service to unauthenticated state, and render the production-ready Google Drive login page instead of plain text.
- `/status` must return JSON with at least `authValid`, `mounted`, `state`, and `message`.
- `/status.state` must be one of `unauthenticated`, `authenticating`, `authenticated`, `mounting`, `mounted`, or `error`.
- The service must create rclone config from Google OAuth credentials before mounting.
- Google Drive must be mounted with `rclone mount`.
- `rclone mount` must poll Google Drive-side changes at least once per 10 minutes.
- `rclone mount` must use VFS write cache mode `writes`, so tools can use filesystem operations such as rewriting existing files in-place, seek/truncate, random writes, and opening files for read/write.
- Cached VFS writes must use an explicit write-back delay of `5s` before upload to Google Drive after file changes are closed/flushed.
- `/status` must report `mounted=true` only after `rclone mount` starts successfully and the container path is verified as a mountpoint.
- The service must request Docker runtime capabilities required for FUSE, including `/dev/fuse`, `cap_add`, and security options.
- The service must record `MountMetadata` so mount-aware plugins can use it.
- Google Drive `MountMetadata` must identify the remote mount source using `remote_name`, use the Google Drive folder name as its display basename, and must not imply a local host directory.
- The service must fail fast instead of silently using a local directory when Google Drive is not mounted.
- The service must not expose or depend on local host directory mounts.
- The service must not configure OpenCode persistence.

## Sub-services
Не выделяются.
