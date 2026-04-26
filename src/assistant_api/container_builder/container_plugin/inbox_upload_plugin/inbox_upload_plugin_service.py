from __future__ import annotations

import base64
from pathlib import Path

from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.container_builder.container_plugin import MOUNT_METADATA_STATE_KEY
from assistant_api.models import (
    ContainerManagedProcess,
    ContainerRuntimeContext,
    ContainerSpec,
    ContainerStartupTask,
    ImageSpec,
    MountMetadata,
)

UPLOAD_HANDLER_PATH = "/opt/notes-assistant-api/inbox_upload_handler.py"


class InboxUploadPluginService:
    name = "inbox-upload"

    def __init__(
        self,
        host_port: int = 8090,
        container_port: int | None = None,
        upload_endpoint_path: str = "/api/inbox/upload",
    ) -> None:
        self.host_port = self._validate_port("host_port", host_port)
        self.container_port = self._validate_port(
            "container_port",
            container_port if container_port is not None else host_port,
        )
        self.upload_endpoint_path = self._validate_upload_endpoint_path(
            upload_endpoint_path
        )

    def configure_image(self, image: ImageSpec) -> None:
        image.run_commands.append("apk add --no-cache python3")
        image.run_commands.append(
            "python3 -m pip install fastapi uvicorn python-multipart"
        )
        image.run_commands.extend(_install_upload_handler_commands())

    def configure_container(self, container: ContainerSpec) -> None:
        mount = self._mount_metadata(container.state)
        container_path = str(mount.container_path)
        container.startup_tasks.append(
            ContainerStartupTask(
                name="create-inbox",
                command=["mkdir", "-p", f"{container_path}/inbox"],
            )
        )
        container.managed_processes.append(
            ContainerManagedProcess(
                name="inbox-upload-server",
                command=[
                    "/bin/sh",
                    "-lc",
                    _upload_server_command(
                        container_path=container_path,
                        container_port=self.container_port,
                        upload_endpoint_path=self.upload_endpoint_path,
                    ),
                ],
            )
        )
        container.ports[self.container_port] = self.host_port

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        return None

    @staticmethod
    def _validate_port(name: str, value: int) -> int:
        if not isinstance(value, int) or value < 1 or value > 65535:
            raise ConfigurationError(f"{name} must be an integer TCP port")
        return value

    @staticmethod
    def _validate_upload_endpoint_path(value: str) -> str:
        if (
            not isinstance(value, str)
            or not value.strip()
            or not value.startswith("/")
            or value == "/"
        ):
            raise ConfigurationError(
                "upload_endpoint_path must start with / and not be just /"
            )
        return value

    @staticmethod
    def _mount_metadata(state: dict[str, object]) -> MountMetadata:
        metadata = state.get(MOUNT_METADATA_STATE_KEY)
        if not isinstance(metadata, MountMetadata):
            raise ConfigurationError(
                "InboxUploadPluginService requires mount metadata"
            )
        return metadata


def _install_upload_handler_commands() -> list[str]:
    module_dir = Path(__file__).parent
    handler_content = module_dir.joinpath("_upload_handler.py").read_bytes()
    return _install_file_commands(UPLOAD_HANDLER_PATH, handler_content)


def _install_file_commands(target_path: str, content: bytes) -> list[str]:
    encoded = base64.b64encode(content).decode("ascii")
    commands = [
        "python3 -c "
        + repr(
            "import pathlib; "
            f"target = pathlib.Path({target_path!r}); "
            "target.parent.mkdir(parents=True, exist_ok=True); "
            "target.write_bytes(b'')"
        )
    ]
    for index in range(0, len(encoded), 48_000):
        chunk = encoded[index : index + 48_000]
        commands.append(
            "python3 -c "
            + repr(
                "import base64, pathlib; "
                f"pathlib.Path({target_path!r}).open('ab').write("
                f"base64.b64decode({chunk!r}))"
            )
        )
    return commands


def _upload_server_command(
    container_path: str,
    container_port: int,
    upload_endpoint_path: str,
) -> str:
    return (
        f"INBOX_CONTAINER_PATH={container_path!r} "
        f"INBOX_ENDPOINT_PATH={upload_endpoint_path!r} "
        f"INBOX_PORT={container_port} "
        f"exec python3 {UPLOAD_HANDLER_PATH}"
    )
