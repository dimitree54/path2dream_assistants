from __future__ import annotations

import shlex
from pathlib import PurePosixPath
from typing import Iterable

from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.models import ContainerStartupTask, LocalSkillPostInstallCommand


def post_install_startup_tasks(
    commands: Iterable[LocalSkillPostInstallCommand],
) -> list[ContainerStartupTask]:
    return [
        ContainerStartupTask(
            name=_task_name(command),
            command=["/bin/sh", "-lc", _post_install_script(command)],
        )
        for command in commands
    ]


def validate_post_install_commands(
    commands: Iterable[LocalSkillPostInstallCommand],
) -> tuple[LocalSkillPostInstallCommand, ...]:
    validated = tuple(commands)
    for command in validated:
        _validate_command(command)
    return validated


def _validate_command(command: LocalSkillPostInstallCommand) -> None:
    if not command.name:
        raise ConfigurationError("post_install_commands name must be non-empty")
    _validate_working_dir(command.working_dir, command.name)
    if not command.command:
        raise ConfigurationError(
            f"post_install_commands command must be non-empty: {command.name}"
        )


def _validate_working_dir(working_dir: PurePosixPath, command_name: str) -> None:
    if working_dir.is_absolute():
        raise ConfigurationError(
            f"post_install_commands working_dir must be relative: {command_name}"
        )
    if str(working_dir) in {"", "."}:
        raise ConfigurationError(
            f"post_install_commands working_dir must be non-empty: {command_name}"
        )
    if "." in working_dir.parts or ".." in working_dir.parts:
        raise ConfigurationError(
            f"post_install_commands working_dir must stay under opencode: {command_name}"
        )


def _task_name(command: LocalSkillPostInstallCommand) -> str:
    return f"post-install-{command.name}"


def _post_install_script(command: LocalSkillPostInstallCommand) -> str:
    return "\n".join(
        [
            "set -eu",
            ': "${XDG_CONFIG_HOME:?XDG_CONFIG_HOME is required}"',
            'config_dir="$XDG_CONFIG_HOME/opencode"',
            f"cd \"$config_dir\"/{shlex.quote(str(command.working_dir))}",
            shlex.join(command.command),
        ]
    )
