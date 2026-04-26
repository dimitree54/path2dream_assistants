from __future__ import annotations

import base64
from pathlib import Path

from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.models import (
    ContainerManagedProcess,
    ContainerRuntimeContext,
    ContainerSpec,
    ImageSpec,
)

from ._auth_server import (
    OpenAIProviderAuthServer,
    OpenAIProviderLoginError,
    required_port_env,
)


AUTH_SERVER_PATH = "/opt/notes-assistant-api/openai_provider_auth_server.py"


class OpenAIProviderLoginPluginService:
    name = "openai-provider-login"

    def __init__(self) -> None:
        try:
            self.opencode_api_port = required_port_env("OPENCODE_API_PORT")
            self.auth_port = required_port_env("OPENAI_AUTH_PORT")
            self._auth_server = OpenAIProviderAuthServer(
                opencode_api_port=self.opencode_api_port,
                auth_port=self.auth_port,
            )
        except OpenAIProviderLoginError as error:
            raise ConfigurationError(str(error)) from error

    def configure_image(self, image: ImageSpec) -> None:
        image.run_commands.append("apk add --no-cache python3")
        image.run_commands.append(_install_auth_server_command())

    def configure_container(self, container: ContainerSpec) -> None:
        container.env["OPENCODE_API_PORT"] = str(self.opencode_api_port)
        container.env["OPENAI_AUTH_PORT"] = str(self.auth_port)
        container.ports[self.auth_port] = self.auth_port
        container.managed_processes.append(
            ContainerManagedProcess(
                name="openai-provider-login",
                command=["/bin/sh", "-lc", _auth_server_command()],
            )
        )

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        if type(runtime.docker_client) is not object:
            return None
        try:
            self._auth_server.start_in_thread("127.0.0.1")
        except OpenAIProviderLoginError as error:
            raise ConfigurationError(str(error)) from error
        return None

    def serve_forever(self) -> None:
        try:
            self._auth_server.serve_forever("0.0.0.0")
        except OpenAIProviderLoginError as error:
            raise ConfigurationError(str(error)) from error


def _install_auth_server_command() -> str:
    source = Path(__file__).with_name("_auth_server.py").read_text(encoding="utf-8")
    encoded_source = base64.b64encode(source.encode("utf-8")).decode("ascii")
    return (
        "mkdir -p /opt/notes-assistant-api && "
        "python3 -c "
        + repr(
            "import base64, pathlib; "
            f"pathlib.Path({AUTH_SERVER_PATH!r}).write_bytes("
            f"base64.b64decode({encoded_source!r}))"
        )
    )


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
