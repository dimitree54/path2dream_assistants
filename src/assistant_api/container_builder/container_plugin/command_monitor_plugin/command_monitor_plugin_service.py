from __future__ import annotations

import base64
from pathlib import Path, PurePosixPath

from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.models import (
    ContainerRuntimeContext,
    ContainerSpec,
    ContainerStartupTask,
    ImageSpec,
    VolumeMount,
)


PLUGIN_SOURCE_IMAGE_PATH = "/opt/notes-assistant-api/command_monitor_plugin.js"
OPENCODE_PLUGIN_FILE_NAME = "notes-assistant-command-monitor.js"
LOG_DIR = PurePosixPath("/tmp/notes-assistant/command-monitor")
FAILED_COMMANDS_LOG_FILE = LOG_DIR / "failed-commands.jsonl"


class CommandMonitorPluginService:
    name = "command-monitor"

    def __init__(self, log_volume: str) -> None:
        self.log_volume = _validate_log_volume(log_volume)

    def configure_image(self, image: ImageSpec) -> None:
        image.apk_packages.append("python3")
        image.run_commands.extend(_install_plugin_source_commands())

    def configure_container(self, container: ContainerSpec) -> None:
        container.volumes[self.log_volume] = VolumeMount(
            source=self.log_volume,
            target=LOG_DIR,
            type="volume",
        )
        container.startup_tasks.append(
            ContainerStartupTask(
                name="install-command-monitor-plugin",
                command=["/bin/sh", "-lc", _install_command()],
            )
        )

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        result = runtime.exec(["/bin/sh", "-lc", _health_command()])
        if result.exit_code != 0:
            raise RuntimeError(
                f"command monitor health check failed: {result.output}"
            )


def _validate_log_volume(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ConfigurationError("log_volume must be a non-empty volume name")
    return value


def _install_command() -> str:
    return "\n".join(
        [
            "set -eu",
            ': "${XDG_CONFIG_HOME:?XDG_CONFIG_HOME is required}"',
            'plugins_dir="$XDG_CONFIG_HOME/opencode/plugins"',
            f"plugin_source={PLUGIN_SOURCE_IMAGE_PATH}",
            'test -r "$plugin_source"',
            'mkdir -p "$plugins_dir"',
            f'cp -f "$plugin_source" "$plugins_dir/{OPENCODE_PLUGIN_FILE_NAME}"',
            f'test -r "$plugins_dir/{OPENCODE_PLUGIN_FILE_NAME}"',
            f"mkdir -p {LOG_DIR}",
            f"test -d {LOG_DIR}",
            f"test -w {LOG_DIR}",
        ]
    )


def _health_command() -> str:
    return "\n".join(
        [
            "set -eu",
            ': "${XDG_CONFIG_HOME:?XDG_CONFIG_HOME is required}"',
            f'test -r "$XDG_CONFIG_HOME/opencode/plugins/{OPENCODE_PLUGIN_FILE_NAME}"',
            f"test -d {LOG_DIR}",
            f'probe="{LOG_DIR}/.notes-assistant-command-monitor-health-$$"',
            'printf "%s" ok > "$probe"',
            'test "$(cat "$probe")" = ok',
            'rm -f "$probe"',
        ]
    )


def _install_plugin_source_commands() -> list[str]:
    content = Path(__file__).parent.joinpath("_command_monitor_plugin.js").read_bytes()
    return _install_file_commands(PLUGIN_SOURCE_IMAGE_PATH, content)


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
