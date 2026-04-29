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
            raise ConfigurationError("plugin_names must contain at least one plugin name")

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
        self._target_dir: str | None = None

    def configure_image(self, image: ImageSpec) -> None:
        image.run_commands.append("apk add --no-cache git python3")

    def configure_container(self, container: ContainerSpec) -> None:
        if container.working_dir is None:
            raise ConfigurationError("SkillsSyncPluginService requires working_dir")

        self._target_dir = str(container.working_dir)
        container.startup_tasks.append(
            ContainerStartupTask(
                name="install-opencode-artifact-bundles",
                command=["/bin/sh", "-lc", self._install_command(self._target_dir)],
            )
        )

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        if self._target_dir is None:
            raise RuntimeError("skills sync target directory was not configured")
        result = runtime.exec(
            [
                "/bin/sh",
                "-lc",
                _installed_artifacts_health_command(self._target_dir),
            ]
        )
        if result.exit_code != 0:
            raise RuntimeError(f"skills sync health check failed: {result.output}")

    def _install_command(self, target_dir: str) -> str:
        quoted_plugins = " ".join(shlex.quote(name) for name in self.plugin_names)
        return "\n".join(
            [
                "set -eu",
                f"target_dir={shlex.quote(target_dir)}",
                f"repo_url={shlex.quote(self.repo_url)}",
                f"repo_ref={shlex.quote(self.repo_ref)}",
                "if [ -e \"$target_dir/AGENTS.md\" ]; then",
                "  printf '%s\\n' 'Error: target AGENTS.md already exists.' >&2",
                "  exit 1",
                "fi",
                "if [ -e \"$target_dir/.opencode/agents\" ]; then",
                "  printf '%s\\n' 'Error: target .opencode/agents already exists.' >&2",
                "  exit 1",
                "fi",
                "if [ -e \"$target_dir/.opencode/skills\" ]; then",
                "  printf '%s\\n' 'Error: target .opencode/skills already exists.' >&2",
                "  exit 1",
                "fi",
                "tmp_dir=$(mktemp -d)",
                "cleanup() { rm -rf \"$tmp_dir\"; }",
                "trap cleanup EXIT INT TERM",
                "git clone --depth 1 --branch \"$repo_ref\" \"$repo_url\" \"$tmp_dir/repo\"",
                (
                    "python3 \"$tmp_dir/repo/install_plugins.py\" "
                    f"--target \"$target_dir\" {quoted_plugins}"
                ),
            ]
        )


def _installed_artifacts_health_command(target_dir: str) -> str:
    return "\n".join(
        [
            "set -eu",
            f"target_dir={shlex.quote(target_dir)}",
            'test -f "$target_dir/AGENTS.md"',
            'test -d "$target_dir/.opencode"',
            (
                "find \"$target_dir/.opencode\" -mindepth 2 -type f "
                "| grep -q ."
            ),
        ]
    )
