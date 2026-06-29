from __future__ import annotations

import os
import shutil
import socket
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.models import ContainerRuntimeContext, ContainerSpec


@dataclass(slots=True)
class StartupResult:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return self.stdout + self.stderr


def service_class() -> type[Any]:
    from assistant_api.container_builder.container_plugin.local_skills_plugin import (
        LocalSkillsPluginService,
    )

    return LocalSkillsPluginService


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_source(
    root: Path,
    *,
    skill_name: str = "private-skill",
    agent_name: str | None = "private-agent.md",
    include_agents_md: bool = True,
    include_config: bool = True,
) -> Path:
    write_file(
        root / ".opencode" / "skills" / skill_name / "SKILL.md",
        f"private skill marker: {skill_name}\n",
    )
    write_file(
        root / ".opencode" / "skills" / skill_name / "references" / "notes.md",
        "private nested reference\n",
    )
    if agent_name is not None:
        write_file(root / ".opencode" / "agents" / agent_name, "private agent\n")
    if include_agents_md:
        write_file(root / "AGENTS.md", "private root rules\n")
    if include_config:
        write_file(root / "opencode.json", '{"model": "private-model"}\n')
    return root


def prepare_container(source_path: Path) -> ContainerSpec:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[service_class()(source_path)]
    )._prepare_specs()
    return container_spec


def only_startup_task(container_spec: ContainerSpec) -> Any:
    assert len(container_spec.startup_tasks) == 1
    task = container_spec.startup_tasks[0]
    assert isinstance(task.name, str)
    assert task.name
    assert isinstance(task.command, list)
    assert task.command[:2] == ["/bin/sh", "-lc"]
    assert task.owner_plugin_name == "local-skills"
    return task


def opencode_config_dir(home: Path) -> Path:
    return home / ".config" / "opencode"


def run_startup_task(
    task: Any,
    *,
    home: Path | None = None,
    include_xdg_config_home: bool = True,
) -> StartupResult:
    env = os.environ.copy()
    if home is not None:
        env["HOME"] = str(home)
        if include_xdg_config_home:
            env["XDG_CONFIG_HOME"] = str(home / ".config")
        else:
            env.pop("XDG_CONFIG_HOME", None)

    result = subprocess.run(
        task.command,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    return StartupResult(
        exit_code=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


@contextmanager
def simulated_source_mount(container_spec: ContainerSpec, source_path: Path) -> Any:
    mount = container_spec.volumes[str(source_path.resolve())]
    target = Path(str(mount.target))
    if target.exists():
        raise AssertionError(f"simulated mount target already exists: {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(source_path.resolve(), target_is_directory=True)
    try:
        yield target
    finally:
        if target.is_symlink():
            target.unlink()
        _remove_empty_parents_until(target.parent, Path("/tmp/notes-assistant"))


def _remove_empty_parents_until(path: Path, stop_at: Path) -> None:
    current = path
    while current != stop_at.parent and str(current).startswith(str(stop_at)):
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def assert_startup_task_succeeds(task: Any, *, home: Path) -> StartupResult:
    result = run_startup_task(task, home=home)
    assert result.exit_code == 0, result.output
    return result


def available_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
        return
    path.unlink()


class RecordingContainer:
    def __init__(self, exit_code: int = 0, output: str = "") -> None:
        self.exit_code = exit_code
        self.output = output
        self.commands: list[list[str]] = []

    def exec_run(self, command: list[str]) -> object:
        self.commands.append(command)
        if len(command) != 3 or command[:2] != ["/bin/sh", "-lc"]:
            return _ExecResult(127, "unknown command")
        return _ExecResult(self.exit_code, self.output)


class _ExecResult:
    def __init__(self, exit_code: int, output: str) -> None:
        self.exit_code = exit_code
        self.output = output.encode("utf-8")


def runtime_for(plugin: Any, source_path: Path, container: RecordingContainer) -> ContainerRuntimeContext:
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()
    assert str(source_path.resolve()) in container_spec.volumes
    return ContainerRuntimeContext(
        docker_client=object(),
        container=container,
        state=container_spec.state,
    )
