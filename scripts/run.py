from __future__ import annotations

import logging

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin.command_monitor_plugin import (
    CommandMonitorPluginService,
)
from assistant_api.container_builder.container_plugin.google_drive_mount_plugin import (
    GoogleDriveMountPluginService,
)
from assistant_api.container_builder.container_plugin.inbox_upload_plugin import (
    InboxUploadPluginService,
)
from assistant_api.container_builder.container_plugin.outbox_download_plugin import (
    OutboxDownloadPluginService,
)
from assistant_api.container_builder.container_plugin.google_drive_persistence_plugin import (
    GoogleDrivePersistencePluginService,
)
from assistant_api.container_builder.container_plugin.openai_provider_login_plugin import (
    OpenAIProviderLoginPluginService,
)
from assistant_api.container_builder.container_plugin.opencode_persistence_plugin import (
    OpenCodePersistencePluginService,
)
from assistant_api.container_builder.container_plugin.opencode_web_server_plugin import (
    OpenCodeWebServerPluginService,
)
from assistant_api.container_builder.container_plugin.opencode_server_plugin import (
    OpenCodeServerPluginService,
)
from assistant_api.container_builder.container_plugin.skills_sync_plugin import (
    SkillsSyncPluginService,
)


def main() -> None:
    import doppler_env  # noqa: F401

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    builder = create_builder()
    print(f"google_drive_auth=http://127.0.0.1:4101/login", flush=True)
    running = builder.build_and_run()
    print(f"container={running.name}", flush=True)
    print(f"id={running.id}", flush=True)
    print(f"url=http://127.0.0.1:4321/", flush=True)
    print(f"openai_auth=http://127.0.0.1:4323/login", flush=True)
    print(f"inbox_upload=http://127.0.0.1:8090/api/inbox/upload", flush=True)
    print(f"outbox_download=http://127.0.0.1:8091/api/outbox/download", flush=True)


def create_builder() -> ContainerBuilderService:
    host_port = 4321
    google_drive_auth_port = 4101
    openai_auth_port = 4323
    inbox_port = 8090
    outbox_port = 8091
    return ContainerBuilderService(
        plugins=[
            OpenCodePersistencePluginService(
                config_volume="notes_assistant_api_opencode_config",
                data_volume="notes_assistant_api_opencode_data",
                persist_auth=True,
                persist_chat_history=True,
                persist_opencode_artifacts=False,
                persist_skills=False,
                persist_agents=False,
            ),
            CommandMonitorPluginService(
                log_volume="notes_assistant_api_command_monitor_logs",
            ),
            GoogleDrivePersistencePluginService(
                config_volume="notes_assistant_api_google_drive_config",
                cache_volume="notes_assistant_api_google_drive_cache",
            ),
            GoogleDriveMountPluginService(
                host_port=google_drive_auth_port,
                drive_folder_name="notes",
                enable_local_folder_import=True,
            ),
            OpenCodeServerPluginService(host_port=host_port, wait_for_mount=True),
            SkillsSyncPluginService(["yid-notes-assistant"]),
            OpenAIProviderLoginPluginService(host_port=openai_auth_port),
            InboxUploadPluginService(host_port=inbox_port, wait_for_mount=True),
            OutboxDownloadPluginService(host_port=outbox_port, wait_for_mount=True),
        ]
    )


if __name__ == "__main__":
    main()
