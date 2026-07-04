# Path2Dream AI Assistants

`path2dream_ai_assistants` is a Python package for applications that need to run an OpenCode-based assistant inside a Docker container.

The main use case is not a standalone end-user app. The intended user is another service, for example a Telegram bot, web backend, or automation worker, that wants to start a prepared OpenCode container and then call it through published local HTTP ports.

Your application chooses which container plugins it needs, starts the container through this package, and then uses the resulting OpenCode/API endpoints as part of its own product.

## What This Package Does

The package provides `ContainerBuilderService`, which builds a Docker image dynamically, starts a Docker container, and applies a list of plugin services in the order provided by the caller.

Plugins can:

- mount a workspace into the container;
- start OpenCode Server or OpenCode Web;
- persist OpenCode auth, chats, skills, agents, and config;
- expose browser login pages for OpenAI provider auth or Google Drive auth;
- expose upload/download endpoints for wrapper applications;
- install selected OpenCode skills and agents before OpenCode starts.

The package does not hide Docker. Docker is part of the runtime contract: the machine running your wrapper application must have Docker installed and the Docker daemon must be available to the Python process.

## Installation

After the package is published:

```bash
pip install path2dream_ai_assistants
```

The PyPI distribution name is `path2dream_ai_assistants`. Public Python imports currently live under `assistant_api`.

From a local checkout:

```bash
pip install -e .
```

## Basic Usage

This example starts an OpenCode Server container for a wrapper application. The wrapper owns the bot/backend logic; this package only prepares and runs the assistant container.

```python
from pathlib import Path

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin.inbox_upload_plugin import (
    InboxUploadPluginService,
)
from assistant_api.container_builder.container_plugin.local_dir_mount_plugin import (
    LocalDirMountPluginService,
)
from assistant_api.container_builder.container_plugin.opencode_persistence_plugin import (
    OpenCodePersistencePluginService,
)
from assistant_api.container_builder.container_plugin.opencode_server_plugin import (
    OpenCodeServerPluginService,
)
from assistant_api.container_builder.container_plugin.outbox_download_plugin import (
    OutboxDownloadPluginService,
)


plugins = [
    LocalDirMountPluginService(Path("/srv/my-telegram-bot/assistant-workspace")),
    OpenCodePersistencePluginService(config_volume="my_app_opencode_config", data_volume="my_app_opencode_data"),
    OpenCodeServerPluginService(host_port=4096),
    InboxUploadPluginService(host_port=8090),
    OutboxDownloadPluginService(host_port=8091),
]

builder = ContainerBuilderService(
    plugins=plugins,
    container_name="my-telegram-bot-opencode",
)

running_container = builder.build_and_run()

print(running_container.name)
print("OpenCode API: http://127.0.0.1:4096/")
print("Upload endpoint: http://127.0.0.1:8090/api/inbox/upload")
print("Outbox list endpoint: http://127.0.0.1:8091/api/outbox/list")
```

After `build_and_run()` returns, the Docker container is running. Your Telegram bot or backend can call the published host ports. For example, it can send requests to OpenCode Server on `http://127.0.0.1:4096/`, upload user files through the inbox endpoint, or fetch generated files through the outbox endpoint.

## Plugin Order

Plugin order matters because plugins coordinate through typed container state.

Common ordering rules:

- mount plugins should come before plugins that need a workspace mount;
- persistence plugins should come before the runtime that uses the persisted state;
- OpenCode runtime plugins should come before plugins that call the OpenCode API;
- auth helper plugins should come after the OpenCode runtime they validate;
- upload/download plugins should come after a mount plugin.

If a required dependency is missing or ordered incorrectly, plugins are expected to fail fast instead of silently falling back.

## Available Plugins

### `LocalDirMountPluginService`

Mounts a local host directory into the container. By default it mounts directly to `/workspace`. Use this when the wrapper app owns a local workspace directory and wants OpenCode to operate on those files.

```python
from assistant_api.container_builder.container_plugin.local_dir_mount_plugin import (
    LocalDirMountPluginService,
)

plugin = LocalDirMountPluginService("/srv/my-bot/workspace")
```

### `GoogleDriveMountPluginService`

Mounts a Google Drive folder into the container through `rclone mount`. It exposes a browser login/status/logout service on a configured host port and can optionally show a local folder import UI for initial notes import.

This plugin requires `GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON` with a Google OAuth Web client JSON value. It requests Google Drive access for a visible app-owned folder, not full-drive access.

```python
from assistant_api.container_builder.container_plugin.google_drive_mount_plugin import (
    GoogleDriveMountPluginService,
)

plugin = GoogleDriveMountPluginService(
    host_port=4322,
    drive_folder_name="notes-assistant",
    enable_local_folder_import=True,
)
```

### `GoogleDrivePersistencePluginService`

Persists Google Drive `rclone` config and cache state in Docker named volumes. Compose it with `GoogleDriveMountPluginService` when Google Drive auth should survive container restart, rebuild, and recreate.

```python
from assistant_api.container_builder.container_plugin.google_drive_persistence_plugin import (
    GoogleDrivePersistencePluginService,
)

plugin = GoogleDrivePersistencePluginService(
    config_volume="my_app_google_drive_config",
    cache_volume="my_app_google_drive_cache",
)
```

### `OpenCodeServerPluginService`

Starts headless `opencode serve` inside the container and publishes it to a host port. Use this for wrapper apps that need an API-style OpenCode runtime without exposing the OpenCode Web UI as the primary interface.

```python
from assistant_api.container_builder.container_plugin.opencode_server_plugin import (
    OpenCodeServerPluginService,
)

plugin = OpenCodeServerPluginService(host_port=4096)
```

### `OpenCodeWebServerPluginService`

Starts `opencode web` inside the container and publishes it to a host port. Use this when the wrapper app wants to expose or open the OpenCode Web UI.

```python
from assistant_api.container_builder.container_plugin.opencode_web_server_plugin import (
    OpenCodeWebServerPluginService,
)

plugin = OpenCodeWebServerPluginService(host_port=4096)
```

### `OpenCodePersistencePluginService`

Persists OpenCode state in Docker named volumes. By default it persists provider auth, chat/session history, global OpenCode config artifacts, skills, and agents.

```python
from assistant_api.container_builder.container_plugin.opencode_persistence_plugin import (
    OpenCodePersistencePluginService,
)

plugin = OpenCodePersistencePluginService(
    config_volume="my_app_opencode_config",
    data_volume="my_app_opencode_data",
)
```

You can disable individual persistence categories through constructor flags such as `persist_auth=False`, `persist_chat_history=False`, `persist_skills=False`, or `persist_agents=False`.

### `OpenAIProviderLoginPluginService`

Adds a browser login page and status endpoint for OpenCode's OpenAI provider auth flow. It uses the OpenCode API inside the container, so it must be composed after `OpenCodeServerPluginService` or another plugin that records OpenCode runtime metadata.

```python
from assistant_api.container_builder.container_plugin.openai_provider_login_plugin import (
    OpenAIProviderLoginPluginService,
)

plugin = OpenAIProviderLoginPluginService(host_port=4323)
```

After startup, open `http://127.0.0.1:4323/login` to complete OpenAI provider login in the browser. The status endpoint is available at `http://127.0.0.1:4323/status`.

### `InboxUploadPluginService`

Adds an HTTP endpoint for uploading files into `<mounted_workspace>/inbox`. Wrapper apps can use this to pass user-provided files into the assistant container.

```python
from assistant_api.container_builder.container_plugin.inbox_upload_plugin import (
    InboxUploadPluginService,
)

plugin = InboxUploadPluginService(host_port=8090)
```

Default endpoint: `POST /api/inbox/upload` with multipart field `file`.

### `OutboxDownloadPluginService`

Adds HTTP endpoints for listing and downloading files from `<mounted_workspace>/outbox`. Downloading a file removes it from the outbox after successful transfer.

```python
from assistant_api.container_builder.container_plugin.outbox_download_plugin import (
    OutboxDownloadPluginService,
)

plugin = OutboxDownloadPluginService(host_port=8091)
```

Default endpoints:

- `GET /api/outbox/list`;
- `GET /api/outbox/download/{filename}`.

### `SkillsSyncPluginService`

Installs selected OpenCode artifact bundles from an external repository into the container's global OpenCode config directory before OpenCode starts. Use this to preload agents, skills, `AGENTS.md`, and OpenCode config for the assistant persona your wrapper app needs.

```python
from assistant_api.container_builder.container_plugin.skills_sync_plugin import (
    SkillsSyncPluginService,
)

plugin = SkillsSyncPluginService(["yid-notes-assistant"])
```

### `CommandMonitorPluginService`

Installs an OpenCode plugin that logs every failed `bash` tool command (non-zero exit code) to `/tmp/notes-assistant/command-monitor/failed-commands.jsonl` on a named volume. Use the accumulated log to discover tools and packages missing from the container image. Compose it after `OpenCodePersistencePluginService` when persistence is used.

```python
from assistant_api.container_builder.container_plugin.command_monitor_plugin import (
    CommandMonitorPluginService,
)

plugin = CommandMonitorPluginService(log_volume="my_app_command_monitor_logs")
```

## Example: Telegram Bot Architecture

A typical Telegram bot integration looks like this:

1. The bot process starts.
2. The bot creates a plugin list for the assistant container.
3. The bot calls `ContainerBuilderService(...).build_and_run()`.
4. The container starts OpenCode and any configured helper endpoints.
5. Telegram messages are handled by the bot's own code.
6. When the bot needs AI work, it calls the OpenCode host port exposed by the container.
7. When users send files, the bot can upload them through the inbox endpoint.
8. When OpenCode produces files, the bot can read them from the outbox endpoint and send them back to the user.

In this architecture, this package is the container orchestration layer. It is not the Telegram bot framework, not the user-facing product, and not a replacement for your application logic.

## Public API Boundary

Use the documented service imports under `assistant_api.container_builder` and `assistant_api.container_builder.container_plugin.*`.

Repository helper commands and local development entrypoints are examples for working in this checkout. They are not the intended public API for applications that install the package.

## Development

This repository uses `uv` for local development.

```bash
uv run pytest
```

Some manual tests require external credentials, browser login, or live third-party state and are excluded from the default pytest run.
