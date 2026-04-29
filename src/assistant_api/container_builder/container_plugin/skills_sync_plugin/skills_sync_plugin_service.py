from __future__ import annotations

import shlex

from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.models import (
    ContainerRuntimeContext,
    ContainerSpec,
    ContainerStartupTask,
    ImageSpec,
)


DEFAULT_REPO_URL = "https://github.com/dimitree54/opencode-plugins.git"
DEFAULT_REPO_REF = "main"


class SkillsSyncPluginService:
    name = "skills-sync"

    def __init__(
        self,
        plugin_names: list[str],
        repo_url: str = DEFAULT_REPO_URL,
        repo_ref: str = DEFAULT_REPO_REF,
    ) -> None:
        if not plugin_names:
            raise ConfigurationError(
                "plugin_names must contain at least one plugin name"
            )

        duplicates = sorted(
            name for name in set(plugin_names) if plugin_names.count(name) > 1
        )
        if duplicates:
            raise ConfigurationError(
                "Duplicate plugin names are not allowed: " + ", ".join(duplicates)
            )

        self.plugin_names = list(plugin_names)
        self.repo_url = repo_url
        self.repo_ref = repo_ref

    def configure_image(self, image: ImageSpec) -> None:
        image.apk_packages.extend(["git", "python3"])

    def configure_container(self, container: ContainerSpec) -> None:
        container.startup_tasks.append(
            ContainerStartupTask(
                name="install-opencode-artifact-bundles",
                command=["/bin/sh", "-lc", self._install_command()],
            )
        )

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        result = runtime.exec(
            [
                "/bin/sh",
                "-lc",
                _installed_artifacts_health_command(),
            ]
        )
        if result.exit_code != 0:
            raise RuntimeError(f"skills sync health check failed: {result.output}")

    def _install_command(self) -> str:
        quoted_plugins = " ".join(shlex.quote(name) for name in self.plugin_names)
        return "\n".join(
            [
                "set -eu",
                f"repo_url={shlex.quote(self.repo_url)}",
                f"repo_ref={shlex.quote(self.repo_ref)}",
                "tmp_dir=$(mktemp -d)",
                "cleanup() { rm -rf \"$tmp_dir\"; }",
                "trap cleanup EXIT INT TERM",
                (
                    'config_dir="${XDG_CONFIG_HOME:?'
                    "XDG_CONFIG_HOME is required}/opencode\""
                ),
                (
                    "git clone --depth 1 --branch \"$repo_ref\" "
                    "\"$repo_url\" \"$tmp_dir/repo\""
                ),
                (
                    "python3 \"$tmp_dir/repo/install_plugins_system.py\" "
                    f"--config-dir \"$config_dir\" {quoted_plugins}"
                ),
            ]
        )


def _installed_artifacts_health_command() -> str:
    return "\n".join(
        [
            "set -eu",
            ': "${XDG_CONFIG_HOME:?XDG_CONFIG_HOME is required}"',
            'config_dir="$XDG_CONFIG_HOME/opencode"',
            'test -d "$config_dir"',
            (
                'if [ -f "$config_dir/AGENTS.md" ] '
                '|| [ -f "$config_dir/opencode.json" ]; then'
            ),
            "  exit 0",
            "fi",
            (
                'if find "$config_dir/agents" "$config_dir/skills" '
                "-mindepth 1 -type f 2>/dev/null | grep -q .; then"
            ),
            "  exit 0",
            "fi",
            "printf '%s\\n' 'No system-wide OpenCode artifacts were found.' >&2",
            "exit 1",
        ]
    )
