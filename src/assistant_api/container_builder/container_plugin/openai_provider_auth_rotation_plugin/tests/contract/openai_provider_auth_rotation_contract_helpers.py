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

    def __init__(self, api_container_port: int = 4096) -> None:
        self.api_container_port = api_container_port

    def configure_image(self, image: ImageSpec) -> None:
        return None

    def configure_container(self, container: ContainerSpec) -> None:
        container.working_dir = PurePosixPath("/workspace")
        container.state[OPENCODE_RUNTIME_STATE_KEY] = OpenCodeRuntimeMetadata(
            working_dir=PurePosixPath("/workspace"),
            api_container_port=self.api_container_port,
        )

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        return None


def service_class() -> type[Any]:
    from assistant_api.container_builder.container_plugin.openai_provider_auth_rotation_plugin import (
        OpenAIProviderAuthRotationPluginService,
    )

    return OpenAIProviderAuthRotationPluginService


def unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def write_auth_file(
    path: Path,
    openai: dict[str, Any] | None,
    *,
    extra: dict[str, Any] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(extra or {})
    if openai is not None:
        payload["openai"] = openai
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def api_auth(key: str = TOKEN_VALUE, *, source: str | None = None) -> dict[str, Any]:
    credential: dict[str, Any] = {"type": "api", "key": key}
    if source is not None:
        credential["metadata"] = {"source": source}
    return credential
