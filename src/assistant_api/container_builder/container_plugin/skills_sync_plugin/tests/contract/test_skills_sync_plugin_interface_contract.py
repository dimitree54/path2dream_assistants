from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder._errors import ConfigurationError
from skills_sync_contract_helpers import (
    DEFAULT_REPO_REF,
    DEFAULT_REPO_URL,
    WorkingDirPlugin,
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


def test_configure_container_requires_working_dir() -> None:
    with pytest.raises(ConfigurationError, match="working_dir"):
        ContainerBuilderService(
            plugins=[service_class()(["yid-notes-assistant"])]
        )._prepare_specs()


def test_valid_working_dir_registers_single_startup_task_only(tmp_path: Path) -> None:
    container_spec = prepare_container(["yid-notes-assistant"], tmp_path)
    only_startup_task(container_spec)

    assert container_spec.working_dir.as_posix() == str(tmp_path)
    assert container_spec.env == {}
    assert container_spec.volumes == {}
    assert container_spec.ports == {}
    assert container_spec.command is None
    assert container_spec.devices == []
    assert container_spec.cap_add == []
    assert container_spec.security_opt == []


def test_skills_sync_only_adds_required_image_dependencies(tmp_path: Path) -> None:
    image_spec, _container_spec = ContainerBuilderService(
        plugins=[
            WorkingDirPlugin(tmp_path),
            service_class()(["yid-notes-assistant"]),
        ]
    )._prepare_specs()

    assert image_spec.env == {}
    assert image_spec.workdir is None
    assert image_spec.command is None
    assert image_spec.run_commands == [
        "mkdir -p /workspace",
        "apk add --no-cache git python3",
    ]


def test_configure_image_installs_startup_task_runtime_dependencies(tmp_path: Path) -> None:
    image_spec, _container_spec = ContainerBuilderService(
        plugins=[
            WorkingDirPlugin(tmp_path),
            service_class()(["yid-notes-assistant"]),
        ]
    )._prepare_specs()

    run_commands = "\n".join(image_spec.run_commands)
    assert "git" in run_commands
    assert "python3" in run_commands
