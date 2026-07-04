from __future__ import annotations

import base64
import os
import re
from pathlib import Path

from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.container_builder.container_plugin import OPENCODE_RUNTIME_STATE_KEY
from assistant_api.models import (
    ContainerRuntimeContext,
    ContainerSpec,
    ContainerStartupTask,
    ImageSpec,
    OpenCodeRuntimeMetadata,
)

from ._api_token_auth import OpenAIProviderApiTokenError, validate_openai_opencode_model


API_TOKEN_AUTH_PATH = "/opt/notes-assistant-api/openai_provider_api_token_auth.py"
DEFAULT_API_TOKEN_ENV_VAR = "OPENAI_API_KEY"
DEFAULT_OPENCODE_MODEL = "openai/gpt-5.5"
ENV_VAR_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class OpenAIProviderApiTokenPluginService:
    name = "openai-provider-api-token"

    def __init__(
        self,
        api_token_env_var: str = DEFAULT_API_TOKEN_ENV_VAR,
        opencode_model: str = DEFAULT_OPENCODE_MODEL,
        replace_existing: bool = False,
    ) -> None:
        self.api_token_env_var = _validate_env_var_name(api_token_env_var)
        self.opencode_model = _validate_opencode_model(opencode_model)
        self.replace_existing = _validate_bool("replace_existing", replace_existing)

    def configure_image(self, image: ImageSpec) -> None:
        image.apk_packages.append("python3")
        image.run_commands.extend(_install_helper_commands())

    def configure_container(self, container: ContainerSpec) -> None:
        token = os.environ.get(self.api_token_env_var)
        if token is None or not token:
            raise ConfigurationError(
                f"{self.api_token_env_var} must contain an OpenAI API token"
            )
        if token != token.strip():
            raise ConfigurationError(
                f"{self.api_token_env_var} must not contain surrounding whitespace"
            )
        container.env[self.api_token_env_var] = token
        container.startup_tasks.append(
            ContainerStartupTask(
                name="openai-api-token-auth",
                command=_install_auth_command(self),
            )
        )

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        command = _health_command(
            api_token_env_var=self.api_token_env_var,
            opencode_model=self.opencode_model,
            opencode_api_port=_opencode_api_port(runtime.state),
        )
        result = runtime.exec(command)
        if result.exit_code != 0:
            raise RuntimeError(f"OpenAI API-token auth health check failed: {result.output}")


def _validate_env_var_name(value: object) -> str:
    if not isinstance(value, str) or not ENV_VAR_PATTERN.fullmatch(value):
        raise ConfigurationError("api_token_env_var must be a valid environment variable name")
    return value


def _validate_opencode_model(value: object) -> str:
    try:
        return validate_openai_opencode_model(value)
    except OpenAIProviderApiTokenError as error:
        raise ConfigurationError(str(error)) from error


def _validate_bool(name: str, value: bool) -> bool:
    if not isinstance(value, bool):
        raise ConfigurationError(f"{name} must be a boolean")
    return value


def _opencode_api_port(state: dict[str, object]) -> int | None:
    metadata = state.get(OPENCODE_RUNTIME_STATE_KEY)
    if isinstance(metadata, OpenCodeRuntimeMetadata):
        return metadata.api_container_port
    return None


def _install_auth_command(plugin: OpenAIProviderApiTokenPluginService) -> list[str]:
    command = [
        "python3",
        API_TOKEN_AUTH_PATH,
        "install",
        "--api-token-env-var",
        plugin.api_token_env_var,
        "--opencode-model",
        plugin.opencode_model,
    ]
    if plugin.replace_existing:
        command.append("--replace-existing")
    return command


def _health_command(
    *,
    api_token_env_var: str,
    opencode_model: str,
    opencode_api_port: int | None,
) -> list[str]:
    command = [
        "python3",
        API_TOKEN_AUTH_PATH,
        "health",
        "--api-token-env-var",
        api_token_env_var,
        "--opencode-model",
        opencode_model,
    ]
    if opencode_api_port is not None:
        command.extend(["--opencode-api-port", str(opencode_api_port)])
    return command


def _install_helper_commands() -> list[str]:
    content = Path(__file__).parent.joinpath("_api_token_auth.py").read_bytes()
    return _install_file_commands(API_TOKEN_AUTH_PATH, content)


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
