from __future__ import annotations

import inspect

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.models import ContainerRuntimeContext
from skills_sync_contract_helpers import (
    DEFAULT_REPO_REF,
    DEFAULT_REPO_URL,
    only_startup_task,
    prepare_container,
    service_class,
)


def test_public_service_import_and_init_signature_defaults() -> None:
    signature = inspect.signature(service_class())

    assert list(signature.parameters) == ["plugin_names", "repo_url", "repo_ref"]
    assert signature.parameters["plugin_names"].default is inspect.Parameter.empty
    assert signature.parameters["repo_url"].default == DEFAULT_REPO_URL
    assert signature.parameters["repo_ref"].default == DEFAULT_REPO_REF


def test_init_requires_at_least_one_plugin_name() -> None:
    with pytest.raises(ConfigurationError, match="plugin_names|at least one"):
        service_class()([])


def test_init_rejects_duplicate_plugin_names() -> None:
    with pytest.raises(
        ConfigurationError,
        match="(?i)(duplicate|more than once|plugin)",
    ):
        service_class()(["yid-notes-assistant", "yid-notes-assistant"])


def test_configure_container_does_not_require_working_dir() -> None:
    container_spec = prepare_container(["yid-notes-assistant"])
    task = only_startup_task(container_spec)

    assert container_spec.working_dir is None
    assert container_spec.env == {}
    assert container_spec.volumes == {}
    assert container_spec.ports == {}
    assert container_spec.command is None
    assert container_spec.devices == []
    assert container_spec.cap_add == []
    assert container_spec.security_opt == []
    assert "install_plugins_system.py" in task.command[2]
    assert "--config-dir" in task.command[2]
    assert "XDG_CONFIG_HOME" in task.command[2]
    assert "install_plugins.py --target" not in task.command[2]
    assert "--target" not in task.command[2]


def test_skills_sync_only_adds_required_image_dependencies() -> None:
    image_spec, _container_spec = ContainerBuilderService(
        plugins=[service_class()(["yid-notes-assistant"])]
    )._prepare_specs()

    assert image_spec.env == {}
    assert image_spec.workdir is None
    assert image_spec.command is None
    assert image_spec.apk_packages == ["git", "python3"]
    assert image_spec.python_packages == []
    assert image_spec.run_commands == ["mkdir -p /workspace"]


def test_configure_image_installs_startup_task_runtime_dependencies() -> None:
    image_spec, _container_spec = ContainerBuilderService(
        plugins=[service_class()(["yid-notes-assistant"])]
    )._prepare_specs()

    assert "git" in image_spec.apk_packages
    assert "python3" in image_spec.apk_packages


def test_post_start_checks_installed_artifacts() -> None:
    plugin = service_class()(["yid-notes-assistant"])
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[plugin]
    )._prepare_specs()
    container = _RecordingContainer(exit_code=0)

    plugin.post_start(
        ContainerRuntimeContext(
            docker_client=object(),
            container=container,
            state=container_spec.state,
        )
    )

    assert container.commands
    assert "AGENTS.md" in container.commands[0][2]
    assert "opencode.json" in container.commands[0][2]
    assert "XDG_CONFIG_HOME" in container.commands[0][2]
    assert "$XDG_CONFIG_HOME/opencode" in container.commands[0][2]


def test_post_start_fails_when_installed_artifacts_are_missing() -> None:
    plugin = service_class()(["yid-notes-assistant"])
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[plugin]
    )._prepare_specs()

    with pytest.raises(RuntimeError, match="skills sync health check failed"):
        plugin.post_start(
            ContainerRuntimeContext(
                docker_client=object(),
                container=_RecordingContainer(exit_code=1, output="missing artifacts"),
                state=container_spec.state,
            )
        )


class _RecordingContainer:
    def __init__(self, exit_code: int, output: str = "") -> None:
        self.exit_code = exit_code
        self.output = output
        self.commands: list[list[str]] = []

    def exec_run(self, command: list[str]) -> object:
        self.commands.append(command)
        exit_code = self.exit_code
        output = self.output.encode("utf-8")

        class Result:
            pass

        result = Result()
        result.exit_code = exit_code
        result.output = output
        return result
