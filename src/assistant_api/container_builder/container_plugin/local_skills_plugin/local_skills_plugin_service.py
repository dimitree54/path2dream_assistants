from __future__ import annotations

import hashlib
import os
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.models import (
    ContainerRuntimeContext,
    ContainerSpec,
    ContainerStartupTask,
    ImageSpec,
    LocalSkillPostInstallCommand,
    VolumeMount,
)

from ._post_install import post_install_startup_tasks, validate_post_install_commands


LOCAL_SKILLS_MOUNT_ROOT = PurePosixPath("/tmp/notes-assistant/local-skills")
_DIRECT_SKILLS_OS_METADATA_FILES = frozenset(
    {".DS_Store", "Thumbs.db", "desktop.ini"}
)


@dataclass(frozen=True, slots=True)
class _LocalArtifacts:
    skills: tuple[str, ...]
    agents: tuple[str, ...]
    has_agents_md: bool
    has_config: bool


class LocalSkillsPluginService:
    name = "local-skills"

    def __init__(
        self,
        source_path: str | Path,
        *,
        post_install_commands: list[LocalSkillPostInstallCommand] | None = None,
    ) -> None:
        self.source_path = Path(source_path).expanduser().resolve()
        self._source_container_path = _source_mount_path(self.source_path)
        self._artifacts = _discover_artifacts(self.source_path)
        self._post_install_commands = validate_post_install_commands(
            post_install_commands or ()
        )

    def configure_image(self, image: ImageSpec) -> None:
        return None

    def configure_container(self, container: ContainerSpec) -> None:
        source_key = str(self.source_path)
        mount = VolumeMount(
            source=source_key,
            target=self._source_container_path,
            mode="ro",
            type="bind",
        )
        existing_mount = container.volumes.get(source_key)
        if existing_mount is not None and existing_mount != mount:
            raise ConfigurationError(
                "source_path is already mounted with different container options"
            )

        container.volumes[source_key] = mount
        container.startup_tasks.append(
            ContainerStartupTask(
                name="install-local-opencode-artifacts",
                command=[
                    "/bin/sh",
                    "-lc",
                    _install_command(self._source_container_path, self._artifacts),
                ],
            )
        )
        container.startup_tasks.extend(
            post_install_startup_tasks(self._post_install_commands)
        )

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        result = runtime.exec(
            [
                "/bin/sh",
                "-lc",
                _health_command(self._artifacts),
            ]
        )
        if result.exit_code != 0:
            raise RuntimeError(f"local skills health check failed: {result.output}")


def _discover_artifacts(source_path: Path) -> _LocalArtifacts:
    _require_directory(source_path, "source_path")

    skills_dir = source_path / ".opencode" / "skills"
    if not skills_dir.exists():
        raise ConfigurationError(
            "source_path must contain at least one skill under .opencode/skills"
        )
    _require_directory(skills_dir, ".opencode/skills")

    skills: list[str] = []
    for entry in sorted(skills_dir.iterdir(), key=lambda path: path.name.lower()):
        if not entry.is_dir():
            if entry.name in _DIRECT_SKILLS_OS_METADATA_FILES:
                continue
            raise ConfigurationError(
                f"skill entry must be a directory: {entry.relative_to(source_path)}"
            )
        skill_file = entry / "SKILL.md"
        if not skill_file.is_file():
            raise ConfigurationError(
                f"skill entry must contain SKILL.md: {entry.relative_to(source_path)}"
            )
        _require_readable_tree(entry, "skill entry")
        skills.append(entry.name)

    if not skills:
        raise ConfigurationError(
            "source_path must contain at least one skill under .opencode/skills"
        )

    agents = _discover_agents(source_path)
    has_agents_md = _readable_optional_file(source_path / "AGENTS.md", "AGENTS.md")
    has_config = _readable_optional_file(source_path / "opencode.json", "opencode.json")
    return _LocalArtifacts(
        skills=tuple(skills),
        agents=tuple(agents),
        has_agents_md=has_agents_md,
        has_config=has_config,
    )


def _discover_agents(source_path: Path) -> list[str]:
    agents_dir = source_path / ".opencode" / "agents"
    if not agents_dir.exists():
        return []
    _require_directory(agents_dir, ".opencode/agents")

    agents: list[str] = []
    for entry in sorted(agents_dir.iterdir(), key=lambda path: path.name.lower()):
        if entry.suffix.lower() != ".md":
            continue
        if not entry.is_file():
            raise ConfigurationError(
                f"agent artifact must be a readable file: {entry.relative_to(source_path)}"
            )
        _require_readable_file(entry, "agent artifact")
        agents.append(entry.name)
    return agents


def _require_directory(path: Path, label: str) -> None:
    if not path.exists():
        raise ConfigurationError(f"{label} does not exist: {path}")
    if not path.is_dir():
        raise ConfigurationError(f"{label} must be a directory: {path}")
    if not os.access(path, os.R_OK | os.X_OK):
        raise ConfigurationError(f"{label} must be readable: {path}")


def _require_readable_tree(path: Path, label: str) -> None:
    _require_directory(path, label)
    for entry in path.rglob("*"):
        if entry.is_dir():
            if not os.access(entry, os.R_OK | os.X_OK):
                raise ConfigurationError(f"{label} directory must be readable: {entry}")
            continue
        if entry.is_file():
            _require_readable_file(entry, label)


def _readable_optional_file(path: Path, label: str) -> bool:
    if not path.exists():
        return False
    if not path.is_file():
        raise ConfigurationError(f"{label} must be a file: {path}")
    _require_readable_file(path, label)
    return True


def _require_readable_file(path: Path, label: str) -> None:
    if not os.access(path, os.R_OK):
        raise ConfigurationError(f"{label} must be readable: {path}")


def _source_mount_path(source_path: Path) -> PurePosixPath:
    digest = hashlib.sha256(str(source_path).encode("utf-8")).hexdigest()[:16]
    safe_name = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "_"
        for char in source_path.name
    )
    return LOCAL_SKILLS_MOUNT_ROOT / f"{safe_name or 'source'}-{digest}"


def _install_command(source_dir: PurePosixPath, artifacts: _LocalArtifacts) -> str:
    lines = [
        "set -eu",
        ': "${XDG_CONFIG_HOME:?XDG_CONFIG_HOME is required}"',
        f"source_dir={shlex.quote(str(source_dir))}",
        'config_dir="$XDG_CONFIG_HOME/opencode"',
        'test -d "$source_dir"',
        'test -r "$source_dir"',
        'test -x "$source_dir"',
        _readable_tree_function(),
        'if [ -e "$config_dir" ] && [ ! -d "$config_dir" ]; then',
        "  printf '%s\\n' 'System install target already exists; refusing to overwrite: opencode' >&2",
        "  exit 1",
        "fi",
        'if [ -e "$config_dir/agents" ] && [ ! -d "$config_dir/agents" ]; then',
        "  printf '%s\\n' 'System install target already exists; refusing to overwrite: agents' >&2",
        "  exit 1",
        "fi",
        'if [ -e "$config_dir/skills" ] && [ ! -d "$config_dir/skills" ]; then',
        "  printf '%s\\n' 'System install target already exists; refusing to overwrite: skills' >&2",
        "  exit 1",
        "fi",
    ]
    _append_source_checks(lines, source_dir, artifacts)
    _append_conflict_checks(lines, artifacts)
    lines.extend(['mkdir -p "$config_dir"'])
    _append_copy_commands(lines, source_dir, artifacts)
    _append_health_checks(lines, artifacts)
    return "\n".join(lines)


def _health_command(artifacts: _LocalArtifacts) -> str:
    lines = [
        "set -eu",
        ': "${XDG_CONFIG_HOME:?XDG_CONFIG_HOME is required}"',
        'config_dir="$XDG_CONFIG_HOME/opencode"',
        'test -d "$config_dir"',
        'test -r "$config_dir"',
    ]
    _append_health_checks(lines, artifacts)
    return "\n".join(lines)


def _append_source_checks(
    lines: list[str],
    source_dir: PurePosixPath,
    artifacts: _LocalArtifacts,
) -> None:
    if artifacts.has_agents_md:
        lines.append(f"test -r {_source_path(source_dir, 'AGENTS.md')}")
    if artifacts.has_config:
        lines.append(f"test -r {_source_path(source_dir, 'opencode.json')}")
    for agent in artifacts.agents:
        lines.append(f"test -r {_source_path(source_dir, '.opencode/agents/' + agent)}")
    for skill in artifacts.skills:
        skill_dir = _source_path(source_dir, ".opencode/skills/" + skill)
        lines.extend(
            [
                f"test -d {skill_dir}",
                f"require_readable_tree {skill_dir}",
                f"test -r {_source_path(source_dir, '.opencode/skills/' + skill + '/SKILL.md')}",
            ]
        )


def _append_conflict_checks(lines: list[str], artifacts: _LocalArtifacts) -> None:
    if artifacts.has_agents_md:
        _append_fail_if_exists(lines, '"$config_dir/AGENTS.md"', "AGENTS.md")
    if artifacts.has_config:
        _append_fail_if_exists(lines, '"$config_dir/opencode.json"', "opencode.json")
    for agent in artifacts.agents:
        _append_fail_if_exists(
            lines,
            _target_expr("agents/" + agent),
            "agents/" + agent,
        )
    for skill in artifacts.skills:
        _append_fail_if_exists(
            lines,
            _target_expr("skills/" + skill),
            "skills/" + skill,
        )


def _append_copy_commands(
    lines: list[str],
    source_dir: PurePosixPath,
    artifacts: _LocalArtifacts,
) -> None:
    if artifacts.has_agents_md:
        lines.append(f"cp {_source_path(source_dir, 'AGENTS.md')} \"${{config_dir}}/AGENTS.md\"")
    if artifacts.has_config:
        lines.append(f"cp {_source_path(source_dir, 'opencode.json')} \"${{config_dir}}/opencode.json\"")
    if artifacts.agents:
        lines.append('mkdir -p "$config_dir/agents"')
        for agent in artifacts.agents:
            lines.append(
                f"cp {_source_path(source_dir, '.opencode/agents/' + agent)} "
                f"{_target_expr('agents/' + agent)}"
            )
    lines.append('mkdir -p "$config_dir/skills"')
    for skill in artifacts.skills:
        lines.append(
            f"cp -R {_source_path(source_dir, '.opencode/skills/' + skill)} "
            f"{_target_expr('skills/' + skill)}"
        )


def _append_health_checks(lines: list[str], artifacts: _LocalArtifacts) -> None:
    if artifacts.has_agents_md:
        lines.append('test -r "$config_dir/AGENTS.md"')
    if artifacts.has_config:
        lines.append('test -r "$config_dir/opencode.json"')
    for agent in artifacts.agents:
        lines.append(f"test -r {_target_expr('agents/' + agent)}")
    for skill in artifacts.skills:
        lines.append(f"test -r {_target_expr('skills/' + skill + '/SKILL.md')}")


def _append_fail_if_exists(lines: list[str], target_expr: str, label: str) -> None:
    lines.extend(
        [
            f"if [ -e {target_expr} ]; then",
            (
                "  printf '%s\\n' "
                + shlex.quote(
                    "System install target already exists; refusing to overwrite: "
                    + label
                )
                + " >&2"
            ),
            "  exit 1",
            "fi",
        ]
    )


def _source_path(source_dir: PurePosixPath, relative_path: str) -> str:
    return shlex.quote(str(source_dir / relative_path))


def _target_expr(relative_path: str) -> str:
    return f'"$config_dir"/{shlex.quote(relative_path)}'


def _readable_tree_function() -> str:
    return "\n".join(
        [
            "require_readable_tree() {",
            '  target="$1"',
            '  if find "$target" -type d ! -exec test -r {} \\; -print -quit | grep -q .; then',
            "    printf '%s\\n' \"Source artifact directory is not readable: $target\" >&2",
            "    exit 1",
            "  fi",
            '  if find "$target" -type d ! -exec test -x {} \\; -print -quit | grep -q .; then',
            "    printf '%s\\n' \"Source artifact directory is not searchable: $target\" >&2",
            "    exit 1",
            "  fi",
            '  if find "$target" -type f ! -exec test -r {} \\; -print -quit | grep -q .; then',
            "    printf '%s\\n' \"Source artifact file is not readable: $target\" >&2",
            "    exit 1",
            "  fi",
            "}",
        ]
    )
