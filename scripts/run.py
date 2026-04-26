from __future__ import annotations

import logging

import doppler_env  # noqa

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin.google_drive_mount_plugin import (
    GoogleDriveMountPluginService,
)
from assistant_api.container_builder.container_plugin.openai_provider_login_plugin import (
    OpenAIProviderLoginPluginService,
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
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    host_port = 4321
    google_drive_auth_port = 4101
    openai_auth_port = 4323
    builder = ContainerBuilderService(
        plugins=[
            OpenCodeServerPluginService(host_port=host_port),
            GoogleDriveMountPluginService(
                host_port=google_drive_auth_port,
                drive_folder_name="notes",
            ),
            OpenAIProviderLoginPluginService(host_port=openai_auth_port),
            SkillsSyncPluginService(["yid-notes-assistant"]),
        ]
    )

    running = builder.build_and_run()
    print(f"container={running.name}", flush=True)
    print(f"id={running.id}", flush=True)
    print(f"url=http://127.0.0.1:{host_port}/", flush=True)
    print(f"google_drive_auth=http://127.0.0.1:{google_drive_auth_port}/login", flush=True)
    print(f"openai_auth=http://127.0.0.1:{openai_auth_port}/login", flush=True)


if __name__ == "__main__":
    main()
