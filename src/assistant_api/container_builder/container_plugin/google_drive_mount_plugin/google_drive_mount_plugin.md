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
- монтирует эту Google Drive папку directly into workspace или в явно настроенный workspace subdirectory/container path;
- опционально показывает на mounted success page local folder import control для начального заполнения notes;
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
        workspace_subdir_name: str | None = None,
        *,
        container_path: PurePosixPath | None = None,
        auth_container_port: int | None = None,
        remote_name: str = "gdrive",
        mode: str = "rw",
        oauth_authorize_url: str = "https://accounts.google.com/o/oauth2/v2/auth",
        oauth_token_url: str = "https://oauth2.googleapis.com/token",
        drive_api_base_url: str = "https://www.googleapis.com/drive/v3",
        public_base_url: str | None = None,
        enable_local_folder_import: bool = False,
        host: str | None = None,
    ) -> None:
        pass
```

## Runtime
Runtime-интерфейс не добавляет ничего нового, а наследуется от [[../container_plugin.md|ContainerPluginService]].

When neither `workspace_subdir_name` nor `container_path` is provided, the service must use `/workspace` as the mount target. The Google Drive `drive_folder_name` configures the Google Drive folder name only and must not create an extra container subdirectory by default.

When `workspace_subdir_name` is provided, the service must derive the mount target as:

```python
PurePosixPath("/workspace") / workspace_subdir_name
```

If `container_path` is provided, the service must use it as the mount target. `workspace_subdir_name` and `container_path` are mutually exclusive.

If OpenCode runtime state already exists and the resolved Google Drive mount target equals the recorded OpenCode working directory, configuration must fail fast because direct-workspace mounts must be configured before consumers that use that directory.

The Google Drive auth flow must run fully inside the container. The host/external auth port is `host_port`. The container/internal auth port is `auth_container_port` when provided; otherwise the service may choose its own internal port. Caller-provided environment variables must not be required for either host/external port or Google Drive folder name.

The optional Docker host bind address is configured through `host`. When `host` is not provided, Docker default bind behavior must be preserved. When `host` is provided, the auth port must bind only to that host address.

By default, the browser-visible OAuth redirect URI must be `http://127.0.0.1:<host_port>/oauth/callback`. When `public_base_url` is provided, the OAuth redirect URI must be `<public_base_url>/oauth/callback`. For example, `public_base_url="https://notes-user.example.com"` must produce `https://notes-user.example.com/oauth/callback` in both the OAuth authorize URL and the OAuth token exchange. `public_base_url` affects only browser-visible OAuth callback construction; internal status checks, mount health checks, and container-local services must continue using local/container URLs.

The service must register the Google Drive auth/status/logout/import HTTP server as a managed background process. On startup, that process must restore persisted auth and mount immediately when valid persisted auth exists. If auth is not already available, it must serve `/login` and `/status` without failing; first-time OAuth completes the Google Drive mount only after the user authorizes in the browser.

Consumers that need the configured mount target must choose their own wait-vs-fail behavior. This service publishes auth endpoints and mount metadata, but it does not block container startup until the Google Drive mount is available.

Published endpoints:
- `GET /login`;
- `GET /oauth/callback`;
- `GET /logout`;
- `GET /status`;
- `POST /import/local-folder`, only when local folder import is enabled.

During `post_start`, the service must wait until its container-local `/status` endpoint is reachable and not in `error` state. If persisted auth for the configured remote exists, `post_start` must also verify `mounted=true`, `authValid=true`, mountpoint health, remote readability, and mount filesystem usability.

When `enable_local_folder_import=True`, the mounted success page must expose a guided import notes panel for initial notes population. This panel must let the user choose either individual local files or a local folder. Individual files are imported into the mounted Google Drive folder root. Folder import must upload all regular files recursively.

For folder import, the default UI mode is `Create selected folder`, so selecting `MyNotes` imports files under `MyNotes/...`. The UI must also offer `Import folder contents`, so the selected folder's first path segment is stripped before upload and files are copied directly under the mounted folder root. Imported files must be copied through the container filesystem, so `rclone mount` uploads them to Google Drive on behalf of the app.

Local folder import must only accept requests when Google Drive is already authenticated, mounted, and healthy. If local folder import is disabled or Google Drive is not mounted, import requests must fail clearly. If any target path already exists in the mounted folder, the import must fail with a user-visible error; it must not overwrite or auto-rename existing files. Import must reject absolute paths, path traversal, empty relative paths, and other unsafe submitted relative paths. Google Drive file or folder chooser import is not part of this service version and must not be exposed through the UI or public init API.

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
- The service must accept optional Docker host bind address through init-time configuration as `host`.
- Invalid `host` bind values must fail fast.
- The service must run the Google Drive auth web server inside the container and expose it only through Docker port publishing.
- The service must not start host-side auth servers, host-side HTTP listeners, host-side background threads, or host-side auth flow processes.
- The service must not require the launcher Python process to stay alive after `build_and_run()` for published auth endpoints to remain available.
- The service must not require `GOOGLE_DRIVE_AUTH_PORT` from environment variables.
- The service must accept Google Drive folder name through init-time configuration as `drive_folder_name`.
- The service must not require `GOOGLE_DRIVE_MOUNT_FOLDER_NAME` from environment variables.
- By default the service must mount the configured Google Drive folder directly into `/workspace`.
- If `workspace_subdir_name` is provided, the service must mount into `/workspace/<workspace_subdir_name>`.
- `workspace_subdir_name` must be one safe directory name: not empty, not absolute, not nested, and not `.` or `..`.
- `workspace_subdir_name` and `container_path` are mutually exclusive.
- If `container_path` is provided, the service must use it as the mount target without requiring OpenCode runtime state.
- If OpenCode runtime state already exists and the resolved mount target is the OpenCode working directory, the service must fail fast with an ordering error.
- The service must not register a blocking startup task for first-time Google Drive OAuth.
- The service must register the Google Drive auth/status/logout/import server as a managed process.
- The managed server must restore and verify a persisted mount when valid persisted auth exists.
- The managed server must serve `/login` and `/status` while unauthenticated instead of failing container startup.
- First-time OAuth must mount Google Drive only after the user completes browser authorization.
- The default rclone remote name must be `gdrive`.
- The service must require `GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON`.
- OAuth authorize and token endpoints must be configurable at init time and default to Google OAuth endpoints.
- Custom OAuth endpoints must be sufficient for the full OAuth flow, so local OAuth-compatible providers can be used without live Google OAuth.
- Google Drive API base URL must be configurable at init time and default to `https://www.googleapis.com/drive/v3`.
- Public OAuth base URL must be configurable at init time as `public_base_url` and default to local redirect behavior.
- `public_base_url` must be an absolute public HTTP(S) base URL without query, fragment, username, password, or path other than `/`; invalid values must fail fast.
- `public_base_url` must only affect browser-visible OAuth redirect URI construction and must not change `/status`, mount health checks, or container-local service URLs.
- Local folder import must be disabled by default and configurable at init time as `enable_local_folder_import`.
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
- When local folder import is enabled, the mounted success page must provide a visible local folder import control for recursive initial notes import.
- When local folder import is disabled, `/login` and the mounted success page must not render local folder import UI.
- The local folder import UI must guide the user with clear empty, selected, uploading, finalizing, success, and error messages.
- The local folder import UI must keep the import button disabled until the user selects at least one file.
- The local folder import UI must allow the user to choose different files or a different folder after selection, success, or error.
- The local folder import UI must support both individual file selection and recursive folder selection.
- The local folder import UI must show a folder placement toggle after folder selection only; the default must create the selected folder, and the alternate mode must import the selected folder contents directly.
- The local folder import control must submit files to `POST /import/local-folder` as `multipart/form-data` and preserve each chosen target path in each file part's relative filename.
- `POST /import/local-folder` must copy all submitted regular files into the mounted folder root, preserving relative paths.
- `POST /import/local-folder` must create needed subdirectories for imported relative paths.
- `POST /import/local-folder` must fail clearly when Google Drive is not authenticated, not mounted, or not healthy.
- `POST /import/local-folder` must fail clearly when local folder import is disabled.
- `POST /import/local-folder` must fail clearly if any submitted file would overwrite an existing path in the mounted folder.
- `POST /import/local-folder` must not overwrite existing files and must not auto-rename conflicting files.
- `POST /import/local-folder` must reject path traversal, absolute paths, empty relative paths, and unsafe submitted relative paths.
- `POST /import/local-folder` must not import arbitrary files or folders from Google Drive; v1 supports only local folder import from the user's browser.
- `/logout` must stop the active Google Drive mount, remove stored Google Drive auth and rclone config for this container state, return the service to unauthenticated state, and render the production-ready Google Drive login page instead of plain text.
- `/status` must return JSON with at least `authValid`, `mounted`, `state`, and `message`.
- `/status.state` must be one of `unauthenticated`, `authenticating`, `authenticated`, `mounting`, `mounted`, or `error`.
- The service must create rclone config from Google OAuth credentials before mounting.
- Rclone config token data must include an rclone-compatible `expiry` timestamp, not only Google OAuth `expires_in`, so persisted auth can refresh expired access tokens.
- Persisted rclone configs created by older versions without token `expiry` must be normalized before restore attempts.
- If a persisted token has a future `expiry` but Google rejects the access token as invalid, restore must force a refresh using the persisted refresh token and retry the read probe once.
- Google Drive must be mounted with `rclone mount`.
- The Google Drive mount target must be absent or empty before `rclone mount` starts.
- The service must fail fast with a clear error if the mount target is non-empty before mount.
- The service must not use `rclone mount --allow-non-empty`.
- `rclone mount` must poll Google Drive-side changes at least once per 10 minutes.
- `rclone mount` must use VFS write cache mode `writes`, so tools can use filesystem operations such as rewriting existing files in-place, seek/truncate, random writes, and opening files for read/write.
- Cached VFS writes must use an explicit write-back delay of `5s` before upload to Google Drive after file changes are closed/flushed.
- `/status` must report `mounted=true` only after `rclone mount` starts successfully and the container path is verified as a mountpoint.
- Mounted state must also require a successful read-only remote probe against the configured rclone remote.
- The service must request Docker runtime capabilities required for FUSE, including `/dev/fuse`, `cap_add`, and security options.
- The service must record `MountMetadata` so mount-aware plugins can use it.
- Google Drive `MountMetadata` must identify the remote mount source using `remote_name`, use the Google Drive folder name as its display basename, and must not imply a local host directory.
- The service must fail fast instead of silently using a local directory when Google Drive is not mounted.
- The service must not expose or depend on local host directory mounts.
- The service must not configure OpenCode persistence.
- When persisted auth exists, restore failures must make the managed auth server unhealthy and must be reported through `post_start`.
- When no persisted auth exists, unauthenticated status is healthy for this plugin; mount consumers decide whether to wait or fail.
- Manual live contract tests for this service must run as `pytest.mark.manual` and use a real container, real OAuth login, and real `rclone mount`.
- Manual live contract tests for this service must compose [[../google_drive_persistence_plugin/google_drive_persistence_plugin.md|GoogleDrivePersistencePluginService]] so auth state survives container recreation during the test run.
- Manual live contract tests for this service must verify that `/workspace/test.md` exists in the mounted folder root and its content is exactly `zebra`.
- Manual live contract tests for this service must verify that enabled local folder import recursively copies files into the mounted folder root while preserving relative paths.
- Manual live contract tests for this service must clean only test-created subdirectories in the mounted folder and must call `/logout` at the end of the manual run.

## Sub-services
Не выделяются.
