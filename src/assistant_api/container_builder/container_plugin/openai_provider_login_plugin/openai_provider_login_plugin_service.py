from __future__ import annotations

import base64
from pathlib import Path

from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.container_builder.container_plugin import OPENCODE_RUNTIME_STATE_KEY
from assistant_api.models import (
    ContainerManagedProcess,
    ContainerRuntimeContext,
    ContainerSpec,
    ImageSpec,
    OpenCodeRuntimeMetadata,
)

from ._login_page import LOGO_ASSET_NAME, SHARED_STYLE_ASSET_NAME

AUTH_SERVER_PATH = "/opt/notes-assistant-api/openai_provider_auth_server.py"
LOGIN_PAGE_PATH = "/opt/notes-assistant-api/_login_page.py"
LOGO_ASSET_PATH = f"/opt/notes-assistant-api/assets/{LOGO_ASSET_NAME}"
SHARED_STYLE_ASSET_PATH = f"/opt/notes-assistant-api/assets/{SHARED_STYLE_ASSET_NAME}"


class OpenAIProviderLoginPluginService:
    name = "openai-provider-login"

    def __init__(self, host_port: int, auth_container_port: int | None = None) -> None:
        self.host_port = self._validate_port("host_port", host_port)
        self.auth_container_port = self._validate_port(
            "auth_container_port",
            auth_container_port if auth_container_port is not None else host_port,
        )
        self.opencode_api_port: int | None = None

    def configure_image(self, image: ImageSpec) -> None:
        image.run_commands.append("apk add --no-cache python3")
        image.run_commands.extend(_install_auth_server_commands())

    def configure_container(self, container: ContainerSpec) -> None:
        opencode_runtime = self._opencode_runtime(container.state)
        self.opencode_api_port = opencode_runtime.api_container_port
        if self.opencode_api_port == self.auth_container_port:
            raise ConfigurationError("OpenCode API port and OpenAI auth port must be different")
        container.env["OPENCODE_API_PORT"] = str(self.opencode_api_port)
        container.env["OPENAI_AUTH_PORT"] = str(self.auth_container_port)
        container.ports[self.auth_container_port] = self.host_port
        container.managed_processes.append(
            ContainerManagedProcess(
                name="openai-provider-login",
                command=["/bin/sh", "-lc", _auth_server_command()],
            )
        )

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        return None

    @staticmethod
    def _validate_port(name: str, value: int) -> int:
        if not isinstance(value, int) or value < 1 or value > 65535:
            raise ConfigurationError(f"{name} must be an integer TCP port")
        return value

    @staticmethod
    def _opencode_runtime(state: dict[str, object]) -> OpenCodeRuntimeMetadata:
        metadata = state.get(OPENCODE_RUNTIME_STATE_KEY)
        if not isinstance(metadata, OpenCodeRuntimeMetadata):
            raise ConfigurationError("OpenAIProviderLoginPluginService requires OpenCode runtime metadata")
        return metadata


def _install_auth_server_commands() -> list[str]:
    module_dir = Path(__file__).parent
    files = {
        AUTH_SERVER_PATH: module_dir.joinpath("_auth_server.py").read_bytes(),
        LOGIN_PAGE_PATH: module_dir.joinpath("_login_page.py").read_bytes(),
        LOGO_ASSET_PATH: module_dir.joinpath("assets", LOGO_ASSET_NAME).read_bytes(),
        SHARED_STYLE_ASSET_PATH: module_dir.parent.joinpath(
            "assets", SHARED_STYLE_ASSET_NAME
        ).read_bytes(),
    }
    commands: list[str] = []
    for target_path, content in files.items():
        commands.extend(_install_file_commands(target_path, content))
    return commands


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


def _auth_server_command() -> str:
    return (
        "attempts=0; "
        "until wget -qO- http://127.0.0.1:$OPENCODE_API_PORT/global/health "
        ">/dev/null 2>&1; do "
        "attempts=$((attempts + 1)); "
        "if [ $attempts -ge 30 ]; then "
        "echo 'OpenCode server did not become ready' >&2; exit 1; "
        "fi; "
        "sleep 1; "
        "done; "
        "OPENCODE_API_PORT=$OPENCODE_API_PORT "
        "OPENAI_AUTH_PORT=$OPENAI_AUTH_PORT "
        f"exec python3 {AUTH_SERVER_PATH}"
    )
