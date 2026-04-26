from __future__ import annotations

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin.local_dir_mount_plugin import (
    LocalDirMountPluginService,
)
from assistant_api.container_builder.container_plugin.opencode_persistence_plugin import (
    OpenCodePersistencePluginService,
)
from assistant_api.container_builder.container_plugin.opencode_web_server_plugin import (
    OpenCodeWebServerPluginService,
)
from assistant_api.container_builder.container_plugin.sync_mount_dir_name_plugin import (
    SyncMountDirNamePluginService,
)


def main() -> None:
    host_port = 4321
    builder = ContainerBuilderService(
        plugins=[
            LocalDirMountPluginService("."),
            SyncMountDirNamePluginService(),
            OpenCodePersistencePluginService(),
            OpenCodeWebServerPluginService(host_port=host_port),
        ]
    )

    running = builder.build_and_run()
    print(f"container={running.name}")
    print(f"id={running.id}")
    print(f"url=http://127.0.0.1:{host_port}/")


if __name__ == "__main__":
    main()
