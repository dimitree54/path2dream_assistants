from __future__ import annotations

import base64
from pathlib import Path

from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.container_builder.container_plugin import OPENCODE_RUNTIME_STATE_KEY
from assistant_api.models import (
    ContainerManagedProcess,
    ContainerRuntimeContext,
    ContainerSpec,
    ContainerStartupTask,
    ImageSpec,
    OpenCodeRuntimeMetadata,
    PublishedPort,
)

from ._login_page import LOGO_ASSET_NAME, SHARED_STYLE_ASSET_NAME
from ._opencode_config import OpenCodeConfigError, validate_openai_opencode_model

AUTH_SERVER_PATH = "/opt/notes-assistant-api/openai_provider_auth_server.py"
OPENCODE_CONFIG_PATH = "/opt/notes-assistant-api/_opencode_config.py"
LOGIN_PAGE_PATH = "/opt/notes-assistant-api/_login_page.py"
LOGO_ASSET_PATH = f"/opt/notes-assistant-api/assets/{LOGO_ASSET_NAME}"
SHARED_STYLE_ASSET_PATH = f"/opt/notes-assistant-api/assets/{SHARED_STYLE_ASSET_NAME}"
DEFAULT_OPENCODE_MODEL = "openai/gpt-5.5"


class OpenAIProviderLoginPluginService:
    name = "openai-provider-login"

    def __init__(
        self,
        host_port: int,
        auth_container_port: int | None = None,
        opencode_model: str = DEFAULT_OPENCODE_MODEL,
        host: str | None = None,
    ) -> None:
        self.host_port = self._validate_port("host_port", host_port)
        self.auth_container_port = self._validate_port(
            "auth_container_port",
            auth_container_port if auth_container_port is not None else host_port,
        )
        self.opencode_model = self._validate_opencode_model(opencode_model)
        self.host = self._validate_host(host)
        self.opencode_api_port: int | None = None

    def configure_image(self, image: ImageSpec) -> None:
        image.apk_packages.append("python3")
        image.run_commands.extend(_install_auth_server_commands())

    def configure_container(self, container: ContainerSpec) -> None:
        opencode_runtime = self._opencode_runtime(container.state)
        self.opencode_api_port = opencode_runtime.api_container_port
        if self.opencode_api_port == self.auth_container_port:
            raise ConfigurationError("OpenCode API port and OpenAI auth port must be different")
        container.env["OPENCODE_API_PORT"] = str(self.opencode_api_port)
        container.env["OPENAI_AUTH_PORT"] = str(self.auth_container_port)
        container.env["OPENCODE_MODEL"] = self.opencode_model
        container.ports[self.auth_container_port] = self._published_port()
        container.startup_tasks.append(
            ContainerStartupTask(
                name="openai-opencode-default-model",
                command=["python3", OPENCODE_CONFIG_PATH, self.opencode_model],
            )
        )
        container.managed_processes.append(
            ContainerManagedProcess(
                name="openai-provider-login",
                command=["/bin/sh", "-lc", _auth_server_command()],
            )
        )

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        result = runtime.exec(
            [
                "/bin/sh",
                "-lc",
                _auth_status_health_command(self.auth_container_port),
            ]
        )
        if result.exit_code != 0:
            raise RuntimeError(f"OpenAI provider login health check failed: {result.output}")

    @staticmethod
    def _validate_port(name: str, value: int) -> int:
        if not isinstance(value, int) or value < 1 or value > 65535:
            raise ConfigurationError(f"{name} must be an integer TCP port")
        return value

    def _published_port(self) -> int | PublishedPort:
        if self.host is None:
            return self.host_port
        return PublishedPort(host_port=self.host_port, host=self.host)

    @staticmethod
    def _validate_host(value: str | None) -> str | None:
        if value is None:
            return None
        try:
            PublishedPort(host_port=1, host=value)
        except ValueError as error:
            raise ConfigurationError("host must be an IP address literal") from error
        return value

    @staticmethod
    def _validate_opencode_model(value: object) -> str:
        try:
            return validate_openai_opencode_model(value)
        except OpenCodeConfigError as error:
            raise ConfigurationError(str(error)) from error

    @staticmethod
    def _opencode_runtime(state: dict[str, object]) -> OpenCodeRuntimeMetadata:
        metadata = state.get(OPENCODE_RUNTIME_STATE_KEY)
        if not isinstance(metadata, OpenCodeRuntimeMetadata):
            raise ConfigurationError(
                "OpenAIProviderLoginPluginService requires OpenCode runtime metadata"
            )
        return metadata


def _install_auth_server_commands() -> list[str]:
    module_dir = Path(__file__).parent
    files = {
        AUTH_SERVER_PATH: module_dir.joinpath("_auth_server.py").read_bytes(),
        OPENCODE_CONFIG_PATH: module_dir.joinpath("_opencode_config.py").read_bytes(),
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
        "while ! wget -qO- http://127.0.0.1:$OPENCODE_API_PORT/global/health "
        ">/dev/null 2>&1; do "
        "sleep 1; "
        "done; "
        "OPENCODE_API_PORT=$OPENCODE_API_PORT "
        "OPENAI_AUTH_PORT=$OPENAI_AUTH_PORT "
        "OPENCODE_MODEL=$OPENCODE_MODEL "
        f"exec python3 {AUTH_SERVER_PATH}"
    )


def _auth_status_health_command(auth_container_port: int) -> str:
    return (
        "python3 - <<'PY'\n"
        "import json\n"
        "import sys\n"
        "import time\n"
        "import urllib.error\n"
        "import urllib.request\n"
        f"url = 'http://127.0.0.1:{auth_container_port}/status'\n"
        "deadline = time.monotonic() + 60\n"
        "last_error = ''\n"
        "while time.monotonic() < deadline:\n"
        "    try:\n"
        "        with urllib.request.urlopen(url, timeout=2) as response:\n"
        "            body = response.read().decode('utf-8')\n"
        "    except urllib.error.HTTPError as error:\n"
        "        body = error.read().decode('utf-8', errors='replace')\n"
        "    except Exception as error:\n"
        "        last_error = str(error)\n"
        "        time.sleep(1)\n"
        "        continue\n"
        "    try:\n"
        "        payload = json.loads(body)\n"
        "    except Exception as error:\n"
        "        last_error = f'invalid JSON from /status: {error}: {body}'\n"
        "        time.sleep(1)\n"
        "        continue\n"
        "    state = payload.get('state')\n"
        "    if state in {'error', 'unavailable'}:\n"
        "        raise SystemExit(f'OpenAI provider status is unhealthy: {payload}')\n"
        "    if isinstance(state, str):\n"
        "        raise SystemExit(0)\n"
        "    last_error = f'/status response has no state: {payload}'\n"
        "    time.sleep(1)\n"
        "raise SystemExit(f'OpenAI provider status did not become healthy: {last_error}')\n"
        "PY"
    )
