from __future__ import annotations

import inspect
import logging
from pathlib import Path
from typing import Any

import assistant_api.container_builder.container_builder_service as container_builder_service
from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin.local_dir_mount_plugin import LocalDirMountPluginService
from assistant_api.container_builder.container_plugin.opencode_persistence_plugin import (
    OpenCodePersistencePluginService,
)
from assistant_api.container_builder.container_plugin.opencode_web_server_plugin import (
    OpenCodeWebServerPluginService,
)
from assistant_api.models import ContainerSpec, ImageSpec


class _MissingContainers:
    def get(self, _container_name: str) -> Any:
        raise LookupError("container does not exist")


class _DockerClient:
    containers = _MissingContainers()


class _Container:
    id = "container-id"
    name = "notes-assistant-opencode"


class _ExistingContainer:
    def __init__(self, calls: list[object]) -> None:
        self._calls = calls

    def remove(self, force: bool = False) -> None:
        self._calls.append(("remove", force))


class _ExistingContainers:
    def __init__(self, calls: list[object]) -> None:
        self._calls = calls

    def get(self, container_name: str) -> _ExistingContainer:
        self._calls.append(("get", container_name))
        return _ExistingContainer(self._calls)


class _DockerClientWithExistingContainer:
    def __init__(self, calls: list[object]) -> None:
        self.containers = _ExistingContainers(calls)


def test_init_accepts_plugins_and_container_name() -> None:
    signature = inspect.signature(ContainerBuilderService)

    assert list(signature.parameters) == ["plugins", "container_name"]
    assert signature.parameters["plugins"].default is inspect.Parameter.empty
    assert signature.parameters["container_name"].default == "notes-assistant-opencode"


def test_run_is_not_public_interface() -> None:
    assert not hasattr(ContainerBuilderService(plugins=[]), "run")


def test_build_builds_image_without_exposing_docker_result(monkeypatch) -> None:
    calls = []

    def build_image(_docker_client: Any, image_spec: ImageSpec, image_tag: str) -> object:
        calls.append((image_spec, image_tag))
        return object()

    monkeypatch.setattr(container_builder_service, "build_image", build_image)

    builder = ContainerBuilderService(plugins=[])
    builder._docker_client = _DockerClient()

    assert builder.build() is None
    assert calls[0][1] == "notes-assistant-opencode:latest"


def test_build_and_run_builds_before_starting_container(monkeypatch) -> None:
    calls = []

    def build_image(_docker_client: Any, _image_spec: ImageSpec, _image_tag: str) -> object:
        calls.append("build")
        return object()

    def ensure_named_volumes(_docker_client: Any, _container_spec: ContainerSpec) -> None:
        calls.append("volumes")

    def run_container(_docker_client: Any, _container_spec: ContainerSpec) -> _Container:
        calls.append("run")
        return _Container()

    monkeypatch.setattr(container_builder_service, "build_image", build_image)
    monkeypatch.setattr(container_builder_service, "ensure_named_volumes", ensure_named_volumes)
    monkeypatch.setattr(container_builder_service, "run_container", run_container)

    builder = ContainerBuilderService(plugins=[])
    builder._docker_client = _DockerClient()

    running = builder.build_and_run()

    assert running.name == "notes-assistant-opencode"
    assert calls == ["build", "volumes", "run"]


def test_build_and_run_logs_existing_container_replacement(monkeypatch, caplog) -> None:
    calls: list[object] = []

    def build_image(_docker_client: Any, _image_spec: ImageSpec, _image_tag: str) -> object:
        calls.append("build")
        return object()

    def ensure_named_volumes(_docker_client: Any, _container_spec: ContainerSpec) -> None:
        calls.append("volumes")

    def run_container(_docker_client: Any, _container_spec: ContainerSpec) -> _Container:
        calls.append("run")
        return _Container()

    monkeypatch.setattr(container_builder_service, "build_image", build_image)
    monkeypatch.setattr(container_builder_service, "ensure_named_volumes", ensure_named_volumes)
    monkeypatch.setattr(container_builder_service, "run_container", run_container)
    caplog.set_level(logging.INFO, logger=container_builder_service.__name__)

    builder = ContainerBuilderService(plugins=[], container_name="existing-container")
    builder._docker_client = _DockerClientWithExistingContainer(calls)

    builder.build_and_run()

    assert ("remove", True) in calls
    assert "Removing existing container before start: name=existing-container" in [
        record.getMessage() for record in caplog.records
    ]


def test_minimal_builder_has_no_optional_container_features() -> None:
    _image_spec, container_spec = ContainerBuilderService(plugins=[])._prepare_specs()

    assert container_spec.name == "notes-assistant-opencode"
    assert container_spec.command is None
    assert container_spec.volumes == {}
    assert container_spec.ports == {}
    assert container_spec.env == {}


def test_container_name_is_configurable() -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[],
        container_name="notes-assistant-opencode-worker-1",
    )._prepare_specs()

    assert container_spec.name == "notes-assistant-opencode-worker-1"


def test_full_plugin_composition_uses_public_services(tmp_path: Path) -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[
            LocalDirMountPluginService(tmp_path),
            OpenCodePersistencePluginService(),
            OpenCodeWebServerPluginService(host_port=4097),
        ]
    )._prepare_specs()

    assert container_spec.volumes[str(tmp_path)].target.as_posix() == "/workspace"
    assert container_spec.working_dir.as_posix() == "/workspace"
    assert container_spec.ports == {4096: 4097}
    assert container_spec.env["HOME"] == "/root"
    assert container_spec.command is not None
    assert "opencode web" in container_spec.command[2]
