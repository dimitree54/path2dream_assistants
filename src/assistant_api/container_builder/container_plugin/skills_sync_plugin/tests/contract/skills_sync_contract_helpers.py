from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.models import ContainerRuntimeContext, ContainerSpec, ImageSpec


DEFAULT_REPO_URL = "https://github.com/dimitree54/opencode-plugins.git"
DEFAULT_REPO_REF = "main"


@dataclass(slots=True)
class StartupResult:
    exit_code: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return self.stdout + self.stderr


class WorkingDirPlugin:
    name = "test-working-dir"

    def __init__(self, working_dir: Path) -> None:
        self.working_dir = PurePosixPath(str(working_dir))

    def configure_image(self, image: ImageSpec) -> None:
        return None

    def configure_container(self, container: ContainerSpec) -> None:
        container.working_dir = self.working_dir

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        return None


def service_class() -> type[Any]:
    from assistant_api.container_builder.container_plugin.skills_sync_plugin import (
        SkillsSyncPluginService,
    )

    return SkillsSyncPluginService


def prepare_container(
    plugin_names: list[str],
    working_dir: Path,
    *,
    repo_url: str = DEFAULT_REPO_URL,
    repo_ref: str = DEFAULT_REPO_REF,
) -> ContainerSpec:
    plugin = service_class()(plugin_names, repo_url=repo_url, repo_ref=repo_ref)
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[WorkingDirPlugin(working_dir), plugin]
    )._prepare_specs()
    return container_spec


def only_startup_task(container_spec: ContainerSpec) -> Any:
    assert hasattr(container_spec, "startup_tasks"), (
        "ContainerSpec must expose startup_tasks for pre-process setup tasks"
    )
    tasks = getattr(container_spec, "startup_tasks")
    assert isinstance(tasks, list)
    assert len(tasks) == 1

    from assistant_api.models import ContainerStartupTask

    task = tasks[0]
    assert isinstance(task, ContainerStartupTask)
    assert isinstance(task.name, str)
    assert task.name
    assert isinstance(task.command, list)
    assert task.command
    assert all(isinstance(part, str) and part for part in task.command)
    return task


def run_startup_task(task: Any) -> StartupResult:
    result = subprocess.run(
        task.command,
        check=False,
        capture_output=True,
        text=True,
    )
    return StartupResult(
        exit_code=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def assert_startup_task_succeeds(task: Any) -> StartupResult:
    result = run_startup_task(task)
    assert result.exit_code == 0, result.output
    return result


def assert_no_installed_artifacts(target: Path) -> None:
    assert not (target / "AGENTS.md").exists()
    assert not (target / ".opencode" / "agents").exists()
    assert not (target / ".opencode" / "skills").exists()


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def clone_live_repo_with_conflicting_bundles(
    tmp_path: Path,
    *,
    agent_file_name: str | None = None,
    skill_name: str | None = None,
) -> Path:
    repo = tmp_path / "opencode-plugins-conflict-repo"
    subprocess.run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            DEFAULT_REPO_REF,
            DEFAULT_REPO_URL,
            str(repo),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    for bundle_name in ("contract-conflict-one", "contract-conflict-two"):
        if agent_file_name is not None:
            write_file(
                repo / bundle_name / ".opencode" / "agents" / agent_file_name,
                f"# Agent from {bundle_name}\n",
            )
        if skill_name is not None:
            write_file(
                repo / bundle_name / ".opencode" / "skills" / skill_name / "SKILL.md",
                f"# Skill from {bundle_name}\n",
            )
        write_file(repo / bundle_name / "AGENTS.md", f"# Rules from {bundle_name}\n")

    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.email=contract-tests@example.test",
            "-c",
            "user.name=Contract Tests",
            "commit",
            "-m",
            "add conflict contract fixtures",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return repo
