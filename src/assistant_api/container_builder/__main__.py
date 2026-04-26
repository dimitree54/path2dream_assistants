from __future__ import annotations

import argparse
from pathlib import Path

from .container_builder_service import ContainerBuilderService
from .container_plugin.local_dir_mount_plugin import LocalDirMountPluginService
from .container_plugin.opencode_persistence_plugin import OpenCodePersistencePluginService
from .container_plugin.opencode_web_server_plugin import OpenCodeWebServerPluginService
from .container_plugin.sync_mount_dir_name_plugin import SyncMountDirNamePluginService


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m assistant_api.container_builder")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--mount-dir", type=Path)
    run_parser.add_argument("--sync-mount-dir-name", action="store_true")
    run_parser.add_argument("--persist-opencode", action="store_true")
    run_parser.add_argument("--opencode-web", action="store_true")
    run_parser.add_argument("--port", type=int, default=4096)
    run_parser.add_argument("--container-port", type=int, default=4096)
    run_parser.add_argument("--container-name", default="notes-assistant-opencode")

    args = parser.parse_args()
    if args.command == "run":
        plugins = []
        if args.mount_dir is not None:
            plugins.append(LocalDirMountPluginService(args.mount_dir))
        if args.sync_mount_dir_name:
            plugins.append(SyncMountDirNamePluginService())
        if args.persist_opencode:
            plugins.append(OpenCodePersistencePluginService())
        if args.opencode_web:
            plugins.append(
                OpenCodeWebServerPluginService(host_port=args.port, container_port=args.container_port)
            )

        builder = ContainerBuilderService(plugins=plugins, container_name=args.container_name)
        running = builder.build_and_run()
        print(f"container={running.name}")
        print(f"id={running.id}")
        if args.opencode_web:
            print(f"url=http://127.0.0.1:{args.port}/")


if __name__ == "__main__":
    main()
