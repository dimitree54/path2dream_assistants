from __future__ import annotations

import base64
import shlex
from pathlib import Path, PurePosixPath

from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.models import (
    ContainerRuntimeContext,
    ContainerSpec,
    ContainerStartupTask,
    ImageSpec,
    VolumeMount,
)
from ._host_history_database import database_check_lines


PERSISTENCE_ROOT = PurePosixPath("/tmp/notes-assistant/opencode-persistence")
AUTH_PERSISTENCE_DIR = PERSISTENCE_ROOT / "auth"
HISTORY_PERSISTENCE_DIR = PERSISTENCE_ROOT / "history"
CONFIG_ARTIFACTS_PERSISTENCE_DIR = PERSISTENCE_ROOT / "config-artifacts"
SKILLS_PERSISTENCE_DIR = PERSISTENCE_ROOT / "skills"
AGENTS_PERSISTENCE_DIR = PERSISTENCE_ROOT / "agents"
CONFIG_ARTIFACT_FILES = (
    "opencode.json",
    "opencode.jsonc",
    "config.json",
    "tui.json",
    "tui.jsonc",
    "AGENTS.md",
)
CONFIG_ARTIFACT_DIRS = (
    "command",
    "commands",
    "mode",
    "modes",
    "plugin",
    "plugins",
    "tool",
    "tools",
    "theme",
    "themes",
)


class OpenCodePersistencePluginService:
    name = "opencode-persistence"

    def __init__(
        self,
        config_volume: str,
        data_volume: str,
        home: PurePosixPath = PurePosixPath("/root"),
        *,
        persist_auth: bool = True,
        persist_chat_history: bool = True,
        persist_opencode_artifacts: bool = True,
        persist_skills: bool = True,
        persist_agents: bool = True,
        chat_history_host_dir: str | Path | None = None,
    ) -> None:
        self.config_volume = config_volume
        self.data_volume = data_volume
        self.home = home
        self.persist_auth = _validate_bool("persist_auth", persist_auth)
        self.persist_chat_history = _validate_bool(
            "persist_chat_history",
            persist_chat_history,
        )
        self.persist_opencode_artifacts = _validate_bool(
            "persist_opencode_artifacts",
            persist_opencode_artifacts,
        )
        self.persist_skills = _validate_bool("persist_skills", persist_skills)
        self.persist_agents = _validate_bool("persist_agents", persist_agents)
        self.chat_history_host_dir = _validate_chat_history_host_dir(
            chat_history_host_dir,
            persist_chat_history=self.persist_chat_history,
        )

    def configure_image(self, image: ImageSpec) -> None:
        if self.chat_history_host_dir is None:
            return None
        image.apk_packages.extend(["python3", "sqlite"])
        patch_source = Path(__file__).with_name("_sqlite_patch.py").read_bytes()
        encoded = base64.b64encode(patch_source).decode("ascii")
        image.run_commands.append(
            "OPENCODE_SQLITE_PATCH=1 python3 -c "
            + shlex.quote(
                "import base64;exec(base64.b64decode(" + repr(encoded) + "))"
            )
            + " --binary /usr/local/bin/opencode"
        )
        return None

    def configure_container(self, container: ContainerSpec) -> None:
        config_home = self.home / ".config"
        data_home = self.home / ".local/share"
        opencode_config_dir = config_home / "opencode"
        opencode_data_dir = data_home / "opencode"
        container.env.update(
            {
                "HOME": str(self.home),
                "XDG_CONFIG_HOME": str(config_home),
                "XDG_DATA_HOME": str(data_home),
            }
        )
        if self._uses_full_directory_persistence():
            container.volumes[self.config_volume] = VolumeMount(
                source=self.config_volume,
                target=opencode_config_dir,
                type="volume",
            )
            container.volumes[self.data_volume] = VolumeMount(
                source=self.data_volume,
                target=opencode_data_dir,
                type="volume",
            )
            return

        if self.persist_auth:
            self._add_volume(container, self._data_volume("auth"), AUTH_PERSISTENCE_DIR)
        if self.persist_chat_history:
            if self.chat_history_host_dir is None:
                self._add_volume(
                    container,
                    self._data_volume("history"),
                    HISTORY_PERSISTENCE_DIR,
                )
            else:
                self._add_bind_mount(
                    container,
                    self.chat_history_host_dir,
                    HISTORY_PERSISTENCE_DIR,
                )
            container.env["OPENCODE_DB"] = str(HISTORY_PERSISTENCE_DIR / "opencode.db")
        if self.persist_opencode_artifacts:
            self._add_volume(
                container,
                self._config_volume("artifacts"),
                CONFIG_ARTIFACTS_PERSISTENCE_DIR,
            )
        if self.persist_skills:
            self._add_volume(container, self._config_volume("skills"), SKILLS_PERSISTENCE_DIR)
        if self.persist_agents:
            self._add_volume(container, self._config_volume("agents"), AGENTS_PERSISTENCE_DIR)

        container.startup_tasks.append(
            ContainerStartupTask(
                name="opencode-persistence-layout",
                command=[
                    "/bin/sh",
                    "-lc",
                    _persistence_setup_command(
                        str(self.home),
                        str(opencode_config_dir),
                        str(opencode_data_dir),
                        persist_auth=self.persist_auth,
                        persist_chat_history=self.persist_chat_history,
                        persist_opencode_artifacts=self.persist_opencode_artifacts,
                        persist_skills=self.persist_skills,
                        persist_agents=self.persist_agents,
                        validate_host_history=self.chat_history_host_dir is not None,
                    ),
                ],
            )
        )

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        result = runtime.exec(
            [
                "/bin/sh",
                "-lc",
                _persistence_health_command(
                    str(self.home),
                    [str(path) for path in self._health_directories()],
                ),
            ]
        )
        if result.exit_code != 0:
            raise RuntimeError(f"OpenCode persistence health check failed: {result.output}")

    def _uses_full_directory_persistence(self) -> bool:
        if self.chat_history_host_dir is not None:
            return False
        return (
            self.persist_auth
            and self.persist_chat_history
            and self.persist_opencode_artifacts
            and self.persist_skills
            and self.persist_agents
        )

    def _health_directories(self) -> list[PurePosixPath]:
        if self._uses_full_directory_persistence():
            return [
                self.home / ".config" / "opencode",
                self.home / ".local/share" / "opencode",
            ]

        directories: list[PurePosixPath] = []
        if self.persist_auth:
            directories.append(AUTH_PERSISTENCE_DIR)
        if self.persist_chat_history:
            directories.append(HISTORY_PERSISTENCE_DIR)
        if self.persist_opencode_artifacts:
            directories.append(CONFIG_ARTIFACTS_PERSISTENCE_DIR)
        if self.persist_skills:
            directories.append(SKILLS_PERSISTENCE_DIR)
        if self.persist_agents:
            directories.append(AGENTS_PERSISTENCE_DIR)
        return directories

    def _config_volume(self, suffix: str) -> str:
        return f"{self.config_volume}_{suffix}"

    def _data_volume(self, suffix: str) -> str:
        return f"{self.data_volume}_{suffix}"

    @staticmethod
    def _add_volume(
        container: ContainerSpec,
        volume: str,
        target: PurePosixPath,
    ) -> None:
        container.volumes[volume] = VolumeMount(
            source=volume,
            target=target,
            type="volume",
        )

    @staticmethod
    def _add_bind_mount(
        container: ContainerSpec,
        host_path: Path,
        target: PurePosixPath,
    ) -> None:
        source = str(host_path)
        container.volumes[source] = VolumeMount(
            source=source,
            target=target,
            type="bind",
        )


def _validate_bool(name: str, value: bool) -> bool:
    if not isinstance(value, bool):
        raise ConfigurationError(f"{name} must be a boolean")
    return value


def _validate_chat_history_host_dir(
    value: str | Path | None,
    *,
    persist_chat_history: bool,
) -> Path | None:
    if value is None:
        return None
    if not persist_chat_history:
        raise ConfigurationError(
            "chat_history_host_dir requires persist_chat_history=True"
        )
    if not isinstance(value, (str, Path)):
        raise ConfigurationError("chat_history_host_dir must be a path")
    try:
        path = Path(value).expanduser().resolve()
        if not path.exists():
            raise ConfigurationError("chat_history_host_dir must exist")
        if not path.is_dir():
            raise ConfigurationError("chat_history_host_dir must be a directory")
    except ConfigurationError:
        raise
    except OSError as error:
        raise ConfigurationError("chat_history_host_dir is not accessible") from error
    return path


def _persistence_setup_command(
    home: str,
    config_dir: str,
    data_dir: str,
    *,
    persist_auth: bool,
    persist_chat_history: bool,
    persist_opencode_artifacts: bool,
    persist_skills: bool,
    persist_agents: bool,
    validate_host_history: bool,
) -> str:
    lines = [
        "set -eu",
        f"home={shlex.quote(home)}",
        f"config_dir={shlex.quote(config_dir)}",
        f"data_dir={shlex.quote(data_dir)}",
        'mkdir -p "$home" "$config_dir" "$data_dir"',
    ]

    if persist_auth:
        lines.extend(
            [
                f"auth_dir={shlex.quote(str(AUTH_PERSISTENCE_DIR))}",
                'mkdir -p "$auth_dir"',
                'rm -f "$data_dir/auth.json"',
                'ln -s "$auth_dir/auth.json" "$data_dir/auth.json"',
            ]
        )

    if persist_chat_history:
        lines.extend(
            [
                f"history_dir={shlex.quote(str(HISTORY_PERSISTENCE_DIR))}",
                'mkdir -p "$history_dir/storage"',
                'rm -rf "$data_dir/storage"',
                'ln -s "$history_dir/storage" "$data_dir/storage"',
            ]
        )
        if validate_host_history:
            lines.extend(database_check_lines())

    if persist_opencode_artifacts:
        lines.append(f"artifacts_dir={shlex.quote(str(CONFIG_ARTIFACTS_PERSISTENCE_DIR))}")
        lines.append('mkdir -p "$artifacts_dir"')
        for filename in CONFIG_ARTIFACT_FILES:
            lines.extend(
                [
                    f"rm -f \"$config_dir/{filename}\"",
                    f"ln -s \"$artifacts_dir/{filename}\" \"$config_dir/{filename}\"",
                ]
            )
        for dirname in CONFIG_ARTIFACT_DIRS:
            lines.extend(
                [
                    f"mkdir -p \"$artifacts_dir/{dirname}\"",
                    f"rm -rf \"$config_dir/{dirname}\"",
                    f"ln -s \"$artifacts_dir/{dirname}\" \"$config_dir/{dirname}\"",
                ]
            )

    if persist_skills:
        lines.extend(
            [
                f"skills_dir={shlex.quote(str(SKILLS_PERSISTENCE_DIR))}",
                'mkdir -p "$skills_dir"',
                'rm -rf "$config_dir/skills"',
                'ln -s "$skills_dir" "$config_dir/skills"',
            ]
        )

    if persist_agents:
        lines.extend(
            [
                f"agents_dir={shlex.quote(str(AGENTS_PERSISTENCE_DIR))}",
                'mkdir -p "$agents_dir"',
                'rm -rf "$config_dir/agents"',
                'ln -s "$agents_dir" "$config_dir/agents"',
            ]
        )

    return "\n".join(lines)


def _persistence_health_command(home: str, directories: list[str]) -> str:
    lines = [
        "set -eu",
        f"home={shlex.quote(home)}",
        'test -d "$home"',
    ]
    if not directories:
        return "\n".join(lines)

    quoted_directories = " ".join(shlex.quote(directory) for directory in directories)
    lines.extend(
        [
            f"for target in {quoted_directories}; do",
            '  test -d "$target"',
            '  probe="$target/.notes-assistant-persistence-health-$$"',
            '  printf "%s" ok > "$probe"',
            '  test "$(cat "$probe")" = ok',
            '  rm -f "$probe"',
            "done",
        ]
    )
    return "\n".join(lines)
