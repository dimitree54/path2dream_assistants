from __future__ import annotations

import json
import socket
from pathlib import Path, PurePosixPath
from typing import Any

from assistant_api.container_builder.container_plugin import OPENCODE_RUNTIME_STATE_KEY
from assistant_api.models import (
    ContainerRuntimeContext,
    ContainerSpec,
    ImageSpec,
    OpenCodeRuntimeMetadata,
)


TOKEN_ENV_VAR = "OPENAI_API_KEY"
TOKEN_VALUE = "sk-contract-openai-token"


class OpenCodeRuntimeStatePlugin:
    name = "opencode-runtime-state"

    def __init__(self, api_container_port: int) -> None:
        self.api_container_port = api_container_port

    def configure_image(self, image: ImageSpec) -> None:
        return None

    def configure_container(self, container: ContainerSpec) -> None:
        container.state[OPENCODE_RUNTIME_STATE_KEY] = OpenCodeRuntimeMetadata(
            working_dir=PurePosixPath("/workspace"),
            api_container_port=self.api_container_port,
        )

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        return None


class RecordingContainer:
    def __init__(self, *, exit_code: int = 0, output: bytes = b"") -> None:
        self.exit_code = exit_code
        self.output = output
        self.commands: list[list[str]] = []

    def exec_run(self, command: list[str]) -> object:
        self.commands.append(command)

        class Result:
            pass

        result = Result()
        result.exit_code = self.exit_code
        result.output = self.output
        return result


def service_class() -> type[Any]:
    from assistant_api.container_builder.container_plugin.openai_provider_api_token_plugin import (
        OpenAIProviderApiTokenPluginService,
    )

    return OpenAIProviderApiTokenPluginService


def unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload
