from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath

from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.container_builder.container_plugin import OPENCODE_RUNTIME_STATE_KEY
from assistant_api.models import (
    ContainerRuntimeContext,
    ContainerSpec,
    ContainerStartupTask,
    ImageSpec,
    OpenCodeRuntimeMetadata,
    VolumeMount,
)

from ._auth_rotation import (
    AUTH_ROTATION_RESULT_FALLBACK,
    AUTH_ROTATION_RESULT_PATH,
    OpenAIProviderAuthRotationError,
    validate_candidate_auth_file,
    validate_openai_opencode_model,
)


AUTH_ROTATION_PATH = "/opt/notes-assistant-api/openai_provider_auth_rotation.py"
CANDIDATE_MOUNT_ROOT = PurePosixPath("/tmp/notes-assistant/openai-auth-rotation")
DEFAULT_FALLBACK_API_TOKEN_ENV_VAR = "OPENAI_API_KEY"
DEFAULT_OPENCODE_MODEL = "openai/gpt-5.5"
DEFAULT_PROBE_MODEL = "openai/gpt-5.4-mini"
DEFAULT_PROBE_VARIANT = "low"
DEFAULT_PROBE_MESSAGE = "hi"
DEFAULT_PROBE_TIMEOUT_SECONDS = 180
ENV_VAR_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
FALLBACK_AUTH_ALERT_MESSAGE = (
    "OpenAI auth pool failed probe; using OPENAI_API_KEY fallback"
)
_LOGGER = logging.getLogger(__name__)


class OpenAIProviderAuthRotationPluginService:
    name = "openai-provider-auth-rotation"

    def __init__(
        self,
        candidate_auth_files: Sequence[str | Path],
        fallback_api_token_env_var: str = DEFAULT_FALLBACK_API_TOKEN_ENV_VAR,
        opencode_model: str = DEFAULT_OPENCODE_MODEL,
        probe_model: str = DEFAULT_PROBE_MODEL,
        probe_variant: str = DEFAULT_PROBE_VARIANT,
        probe_message: str = DEFAULT_PROBE_MESSAGE,
        probe_expected_text: str | None = None,
        probe_timeout_seconds: int = DEFAULT_PROBE_TIMEOUT_SECONDS,
        on_auth_alert: Callable[[str], None] | None = None,
    ) -> None:
        self.candidate_auth_files = _validate_candidate_auth_files(candidate_auth_files)
        self.fallback_api_token_env_var = _validate_env_var_name(fallback_api_token_env_var)
        self.opencode_model = _validate_opencode_model("opencode_model", opencode_model)
        self.probe_model = _validate_opencode_model("probe_model", probe_model)
        self.probe_variant = _validate_clean_string("probe_variant", probe_variant)
        self.probe_message = _validate_clean_string("probe_message", probe_message)
        self.probe_expected_text = _validate_optional_clean_string(
            "probe_expected_text",
            probe_expected_text,
        )
        self.probe_timeout_seconds = _validate_timeout(probe_timeout_seconds)
        self._on_auth_alert = on_auth_alert

    def configure_image(self, image: ImageSpec) -> None:
        image.apk_packages.append("python3")
        image.run_commands.extend(_install_helper_commands())

    def configure_container(self, container: ContainerSpec) -> None:
        token = os.environ.get(self.fallback_api_token_env_var)
        if token is None or not token:
            raise ConfigurationError(
                f"{self.fallback_api_token_env_var} must contain an OpenAI API token"
            )
        if token != token.strip():
            raise ConfigurationError(
                f"{self.fallback_api_token_env_var} must not contain surrounding whitespace"
            )

        container.env[self.fallback_api_token_env_var] = token
        candidate_mounts = _candidate_mounts(self.candidate_auth_files)
        for source_path, container_path in candidate_mounts:
            container.volumes[str(source_path)] = VolumeMount(
                source=str(source_path),
                target=container_path,
                mode="ro",
                type="bind",
            )

        container.startup_tasks.append(
            ContainerStartupTask(
                name="openai-auth-rotation",
                command=_rotation_command(
                    candidate_paths=[container_path for _source, container_path in candidate_mounts],
                    fallback_api_token_env_var=self.fallback_api_token_env_var,
                    opencode_model=self.opencode_model,
                    probe_model=self.probe_model,
                    probe_variant=self.probe_variant,
                    probe_message=self.probe_message,
                    probe_expected_text=self.probe_expected_text,
                    probe_timeout_seconds=self.probe_timeout_seconds,
                    working_dir=_working_dir(container),
                ),
            )
        )

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        if self._on_auth_alert is None:
            return
        result = _read_rotation_result(runtime)
        if result != AUTH_ROTATION_RESULT_FALLBACK:
            return
        try:
            self._on_auth_alert(FALLBACK_AUTH_ALERT_MESSAGE)
        except Exception:
            _LOGGER.exception(
                "OpenAI auth-rotation fallback alert callback failed"
            )


def _read_rotation_result(runtime: ContainerRuntimeContext) -> str | None:
    exec_result = runtime.exec(
        [
            "/bin/sh",
            "-lc",
            f"cat {AUTH_ROTATION_RESULT_PATH}",
        ]
    )
    if exec_result.exit_code != 0:
        return None
    return exec_result.output.strip() or None


def _validate_candidate_auth_files(values: Sequence[str | Path]) -> tuple[Path, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ConfigurationError("candidate_auth_files must be a non-empty sequence")
    if not values:
        raise ConfigurationError("candidate_auth_files must be a non-empty sequence")

    paths: list[Path] = []
    seen: set[Path] = set()
    for value in values:
        if not isinstance(value, (str, Path)):
            raise ConfigurationError("candidate_auth_files entries must be paths")
        path = Path(value).expanduser().resolve()
        if path in seen:
            raise ConfigurationError("candidate_auth_files entries must be unique")
        seen.add(path)
        if not path.is_file():
            raise ConfigurationError(f"candidate auth file must exist: {path}")
        if not os.access(path, os.R_OK):
            raise ConfigurationError(f"candidate auth file must be readable: {path}")
        try:
            validate_candidate_auth_file(path)
        except OpenAIProviderAuthRotationError as error:
            raise ConfigurationError(str(error)) from error
        paths.append(path)
    return tuple(paths)


def _validate_env_var_name(value: object) -> str:
    if not isinstance(value, str) or not ENV_VAR_PATTERN.fullmatch(value):
        raise ConfigurationError(
            "fallback_api_token_env_var must be a valid environment variable name"
        )
    return value


def _validate_opencode_model(name: str, value: object) -> str:
    try:
        return validate_openai_opencode_model(value, name=name)
    except OpenAIProviderAuthRotationError as error:
        raise ConfigurationError(str(error)) from error


def _validate_clean_string(name: str, value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ConfigurationError(f"{name} must be a non-empty string")
    return value


def _validate_optional_clean_string(name: str, value: object) -> str | None:
    if value is None:
        return None
    return _validate_clean_string(name, value)


def _validate_timeout(value: object) -> int:
    if not isinstance(value, int) or value < 1:
        raise ConfigurationError("probe_timeout_seconds must be a positive integer")
    return value


def _candidate_mounts(paths: tuple[Path, ...]) -> list[tuple[Path, PurePosixPath]]:
    mounts: list[tuple[Path, PurePosixPath]] = []
    for index, path in enumerate(paths):
        digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
        mounts.append((path, CANDIDATE_MOUNT_ROOT / f"candidate-{index:03d}-{digest}.json"))
    return mounts


def _working_dir(container: ContainerSpec) -> PurePosixPath:
    metadata = container.state.get(OPENCODE_RUNTIME_STATE_KEY)
    if isinstance(metadata, OpenCodeRuntimeMetadata):
        return metadata.working_dir
    if container.working_dir is not None:
        return container.working_dir
    return PurePosixPath("/workspace")


def _rotation_command(
    *,
    candidate_paths: list[PurePosixPath],
    fallback_api_token_env_var: str,
    opencode_model: str,
    probe_model: str,
    probe_variant: str,
    probe_message: str,
    probe_expected_text: str | None,
    probe_timeout_seconds: int,
    working_dir: PurePosixPath,
) -> list[str]:
    command = ["python3", AUTH_ROTATION_PATH]
    for path in candidate_paths:
        command.extend(["--candidate-auth-file", str(path)])
    command.extend(
        [
            "--fallback-api-token-env-var",
            fallback_api_token_env_var,
            "--opencode-model",
            opencode_model,
            "--probe-model",
            probe_model,
            "--probe-variant",
            probe_variant,
            "--probe-message",
            probe_message,
            "--probe-timeout-seconds",
            str(probe_timeout_seconds),
            "--working-dir",
            str(working_dir),
        ]
    )
    if probe_expected_text is not None:
        command.extend(["--probe-expected-text", probe_expected_text])
    return command


def _install_helper_commands() -> list[str]:
    content = Path(__file__).parent.joinpath("_auth_rotation.py").read_bytes()
    return _install_file_commands(AUTH_ROTATION_PATH, content)


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
