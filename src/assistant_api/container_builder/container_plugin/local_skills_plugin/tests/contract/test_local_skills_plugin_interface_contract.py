from __future__ import annotations

import inspect
import os
from pathlib import Path, PurePosixPath

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.models import LocalSkillPostInstallCommand, VolumeMount
from local_skills_contract_helpers import (
    RecordingContainer,
    assert_startup_task_succeeds,
    make_source,
    only_startup_task,
    opencode_config_dir,
    prepare_container,
    remove_path,
    run_startup_task,
    runtime_for,
    service_class,
    simulated_source_mount,
    write_file,
)


def test_public_service_import_and_init_signature() -> None:
    service = service_class()
    signature = inspect.signature(service)

    assert list(signature.parameters) == ["source_path", "post_install_commands"]
    assert signature.parameters["source_path"].default is inspect.Parameter.empty
    assert signature.parameters["post_install_commands"].default is None
    assert (
        signature.parameters["post_install_commands"].kind
        is inspect.Parameter.KEYWORD_ONLY
    )


def test_configure_container_rejects_missing_source_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    with pytest.raises(ConfigurationError, match="source_path.*exist"):
        ContainerBuilderService(plugins=[service_class()(missing)])._prepare_specs()


def test_configure_container_rejects_source_path_file(tmp_path: Path) -> None:
    source_file = tmp_path / "source.txt"
    source_file.write_text("not a directory", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="source_path.*directory"):
        ContainerBuilderService(plugins=[service_class()(source_file)])._prepare_specs()


def test_configure_container_rejects_unreadable_source_path(tmp_path: Path) -> None:
    source = make_source(tmp_path / "source")
    source.chmod(0)
    try:
        if os.access(source, os.R_OK | os.X_OK):
            pytest.skip("chmod did not make source unreadable on this platform")
        with pytest.raises(ConfigurationError, match="source_path.*readable"):
            ContainerBuilderService(plugins=[service_class()(source)])._prepare_specs()
    finally:
        source.chmod(0o755)


def test_configure_container_requires_at_least_one_skill(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_file(source / "AGENTS.md", "rules only\n")

    with pytest.raises(ConfigurationError, match="skill"):
        ContainerBuilderService(plugins=[service_class()(source)])._prepare_specs()


def test_configure_container_rejects_skill_entry_without_skill_md(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / ".opencode" / "skills" / "broken-skill").mkdir(parents=True)

    with pytest.raises(ConfigurationError, match="SKILL.md"):
        ContainerBuilderService(plugins=[service_class()(source)])._prepare_specs()


def test_configure_container_rejects_file_in_skills_dir(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_file(source / ".opencode" / "skills" / "not-a-skill.txt", "bad\n")

    with pytest.raises(ConfigurationError, match="skill.*directory"):
        ContainerBuilderService(plugins=[service_class()(source)])._prepare_specs()


def test_configure_container_ignores_os_metadata_files_in_skills_dir(
    tmp_path: Path,
) -> None:
    metadata_files = (".DS_Store", "Thumbs.db", "desktop.ini")
    source = make_source(tmp_path / "source")
    skills_dir = source / ".opencode" / "skills"
    for file_name in metadata_files:
        write_file(skills_dir / file_name, "metadata\n")
    home = tmp_path / "home"
    home.mkdir()

    container_spec = prepare_container(source)
    task = only_startup_task(container_spec)

    command_text = task.command[2]
    assert "private-skill" in command_text
    for file_name in metadata_files:
        assert file_name not in command_text

    with simulated_source_mount(container_spec, source):
        assert_startup_task_succeeds(task, home=home)

    config_dir = opencode_config_dir(home)
    assert (config_dir / "skills" / "private-skill" / "SKILL.md").exists()
    for file_name in metadata_files:
        assert not (config_dir / "skills" / file_name).exists()


def test_configure_container_mounts_source_read_only_outside_workspace(
    tmp_path: Path,
) -> None:
    source = make_source(tmp_path / "source")

    container_spec = prepare_container(source)

    source_key = str(source.resolve())
    mount = container_spec.volumes[source_key]
    assert mount == VolumeMount(
        source=source_key,
        target=mount.target,
        mode="ro",
        type="bind",
    )
    assert not str(mount.target).startswith("/workspace")
    assert "local-skills" in str(mount.target)
    assert container_spec.working_dir is None
    assert container_spec.command is None
    assert container_spec.ports == {}
    assert container_spec.managed_processes == []
    assert not {"HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"} & set(
        container_spec.env
    )


def test_configure_container_registers_startup_task_targeting_xdg_config_home(
    tmp_path: Path,
) -> None:
    source = make_source(tmp_path / "source")

    task = only_startup_task(prepare_container(source))

    command_text = task.command[2]
    assert task.name == "install-local-opencode-artifacts"
    assert "XDG_CONFIG_HOME" in command_text
    assert "$XDG_CONFIG_HOME/opencode" in command_text
    assert "private-skill" in command_text
    assert "private-agent.md" in command_text
    assert "/workspace" not in command_text


def test_configure_container_registers_post_install_tasks_after_artifact_install(
    tmp_path: Path,
) -> None:
    source = make_source(tmp_path / "source", skill_name="clip-editor")
    post_install = LocalSkillPostInstallCommand(
        name="install-remotion-renderer-deps",
        working_dir=PurePosixPath("skills/clip-editor/scripts/remotion_artifact_renderer"),
        command=["npm", "ci"],
    )

    container_spec = prepare_container(
        source,
        post_install_commands=[post_install],
    )

    assert len(container_spec.startup_tasks) == 2
    install_task, post_install_task = container_spec.startup_tasks
    assert install_task.name == "install-local-opencode-artifacts"
    assert "install-remotion-renderer-deps" in post_install_task.name
    assert post_install_task.owner_plugin_name == "local-skills"

    command_text = post_install_task.command[2]
    assert "XDG_CONFIG_HOME" in command_text
    assert "$XDG_CONFIG_HOME/opencode" in command_text
    assert "skills/clip-editor/scripts/remotion_artifact_renderer" in command_text
    assert "npm ci" in command_text
    assert str(source.resolve()) not in command_text


def test_post_install_task_runs_inside_installed_artifact_copy(tmp_path: Path) -> None:
    source = make_source(tmp_path / "source", skill_name="clip-editor")
    home = tmp_path / "home"
    home.mkdir()
    post_install = LocalSkillPostInstallCommand(
        name="write-prepared-marker",
        working_dir=PurePosixPath("skills/clip-editor"),
        command=[
            "/bin/sh",
            "-lc",
            "test -r SKILL.md && printf '%s\\n' \"$PWD\" > postinstall.pwd",
        ],
    )
    container_spec = prepare_container(
        source,
        post_install_commands=[post_install],
    )
    install_task, post_install_task = container_spec.startup_tasks

    with simulated_source_mount(container_spec, source):
        assert_startup_task_succeeds(install_task, home=home)
        assert_startup_task_succeeds(post_install_task, home=home)

    installed_marker = (
        opencode_config_dir(home) / "skills" / "clip-editor" / "postinstall.pwd"
    )
    assert installed_marker.exists()
    assert "skills/clip-editor" in installed_marker.read_text(encoding="utf-8")
    assert not (source / ".opencode" / "skills" / "clip-editor" / "postinstall.pwd").exists()


@pytest.mark.parametrize(
    "post_install",
    [
        LocalSkillPostInstallCommand(
            name="",
            working_dir=PurePosixPath("skills/private-skill"),
            command=["true"],
        ),
        LocalSkillPostInstallCommand(
            name="absolute",
            working_dir=PurePosixPath("/skills/private-skill"),
            command=["true"],
        ),
        LocalSkillPostInstallCommand(
            name="traversal",
            working_dir=PurePosixPath("skills/../private-skill"),
            command=["true"],
        ),
        LocalSkillPostInstallCommand(
            name="empty-command",
            working_dir=PurePosixPath("skills/private-skill"),
            command=[],
        ),
    ],
)
def test_configure_container_rejects_invalid_post_install_commands(
    tmp_path: Path,
    post_install: LocalSkillPostInstallCommand,
) -> None:
    source = make_source(tmp_path / "source")

    with pytest.raises(ConfigurationError):
        ContainerBuilderService(
            plugins=[
                service_class()(
                    source,
                    post_install_commands=[post_install],
                )
            ]
        )._prepare_specs()


def test_configure_image_does_not_add_dependencies(tmp_path: Path) -> None:
    source = make_source(tmp_path / "source")

    image_spec, _container_spec = ContainerBuilderService(
        plugins=[service_class()(source)]
    )._prepare_specs()

    assert image_spec.env == {}
    assert image_spec.workdir is None
    assert image_spec.command is None
    assert image_spec.apk_packages == []
    assert image_spec.python_packages == []
    assert image_spec.run_commands == ["mkdir -p /workspace"]


def test_startup_task_installs_artifacts_without_workspace_pollution(
    tmp_path: Path,
) -> None:
    source = make_source(tmp_path / "source")
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    container_spec = prepare_container(source)
    task = only_startup_task(container_spec)

    with simulated_source_mount(container_spec, source):
        assert_startup_task_succeeds(task, home=home)

    config_dir = opencode_config_dir(home)
    assert (config_dir / "skills" / "private-skill" / "SKILL.md").read_text(
        encoding="utf-8"
    ).startswith("private skill marker")
    assert (config_dir / "skills" / "private-skill" / "references" / "notes.md").exists()
    assert (config_dir / "agents" / "private-agent.md").exists()
    assert (config_dir / "AGENTS.md").read_text(encoding="utf-8") == "private root rules\n"
    assert (config_dir / "opencode.json").read_text(encoding="utf-8") == (
        '{"model": "private-model"}\n'
    )
    assert not (workspace / "AGENTS.md").exists()
    assert not (workspace / ".opencode").exists()


@pytest.mark.parametrize(
    ("relative_path", "content", "expected_error_fragment"),
    [
        ("AGENTS.md", "existing rules\n", "AGENTS.md"),
        ("opencode.json", "{}\n", "opencode.json"),
        ("agents/private-agent.md", "existing agent\n", "private-agent.md"),
        ("skills/private-skill/SKILL.md", "existing skill\n", "private-skill"),
    ],
)
def test_startup_task_fails_on_existing_target_artifact_without_partial_install(
    tmp_path: Path,
    relative_path: str,
    content: str,
    expected_error_fragment: str,
) -> None:
    source = make_source(tmp_path / "source")
    home = tmp_path / "home"
    home.mkdir()
    config_dir = opencode_config_dir(home)
    existing_artifact = config_dir / relative_path
    write_file(existing_artifact, content)
    container_spec = prepare_container(source)
    task = only_startup_task(container_spec)

    with simulated_source_mount(container_spec, source):
        result = run_startup_task(task, home=home)

    assert result.exit_code != 0
    assert "refusing to overwrite" in result.output
    assert expected_error_fragment in result.output
    assert existing_artifact.read_text(encoding="utf-8") == content
    assert not (config_dir / "skills" / "private-skill" / "references").exists()
    assert not (config_dir / "agents" / "private-agent.md").exists() or (
        relative_path == "agents/private-agent.md"
    )


def test_startup_task_fails_when_xdg_config_home_is_missing(tmp_path: Path) -> None:
    source = make_source(tmp_path / "source")
    home = tmp_path / "home"
    home.mkdir()
    container_spec = prepare_container(source)
    task = only_startup_task(container_spec)

    with simulated_source_mount(container_spec, source):
        result = run_startup_task(task, home=home, include_xdg_config_home=False)

    assert result.exit_code != 0
    assert "XDG_CONFIG_HOME is required" in result.output


def test_post_start_checks_expected_installed_artifacts(tmp_path: Path) -> None:
    source = make_source(tmp_path / "source")
    plugin = service_class()(source)
    container = RecordingContainer(exit_code=0)

    plugin.post_start(runtime_for(plugin, source, container))

    assert container.commands
    command_text = container.commands[0][2]
    assert "XDG_CONFIG_HOME" in command_text
    assert "$XDG_CONFIG_HOME/opencode" in command_text
    assert "skills/private-skill/SKILL.md" in command_text
    assert "agents/private-agent.md" in command_text
    assert "AGENTS.md" in command_text
    assert "opencode.json" in command_text


def test_post_start_fails_when_expected_artifact_is_missing(tmp_path: Path) -> None:
    source = make_source(tmp_path / "source")
    plugin = service_class()(source)

    with pytest.raises(RuntimeError, match="local skills health check failed"):
        plugin.post_start(
            runtime_for(
                plugin,
                source,
                RecordingContainer(exit_code=1, output="missing skill"),
            )
        )


def test_simulated_post_start_fake_fails_unknown_commands() -> None:
    container = RecordingContainer()

    result = container.exec_run(["unknown"])

    assert result.exit_code == 127
    assert b"unknown command" in result.output
