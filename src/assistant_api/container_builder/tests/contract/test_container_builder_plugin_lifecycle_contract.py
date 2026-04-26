from __future__ import annotations

import logging
from typing import Any

import pytest

import assistant_api.container_builder.container_builder_service as container_builder_service
from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.models import ContainerRuntimeContext, ContainerSpec, ImageSpec


class _MissingContainers:
    def get(self, _container_name: str) -> Any:
        raise LookupError("container does not exist")


class _DockerClient:
    containers = _MissingContainers()


class _Container:
    id = "container-id"
    name = "notes-assistant-opencode"


class _RecordingPlugin:
    def __init__(
        self,
        name: str,
        events: list[tuple[str, str]],
        fail_stage: str | None = None,
    ) -> None:
        self.name = name
        self._events = events
        self._fail_stage = fail_stage

    def configure_image(self, _image: ImageSpec) -> None:
        self._record("configure_image")

    def configure_container(self, _container: ContainerSpec) -> None:
        self._record("configure_container")

    def post_start(self, _runtime: ContainerRuntimeContext) -> None:
        self._record("post_start")

    def _record(self, stage: str) -> None:
        if self._fail_stage == stage:
            raise ValueError(f"{self.name} failed at {stage}")
        self._events.append((self.name, stage))


def test_build_and_run_logs_each_plugin_hook(monkeypatch, caplog) -> None:
    events: list[tuple[str, str]] = []
    _stub_docker_calls(monkeypatch, events)

    caplog.set_level(logging.INFO, logger=container_builder_service.__name__)

    builder = ContainerBuilderService(plugins=[_RecordingPlugin("alpha", events)])
    builder._docker_client = _DockerClient()

    builder.build_and_run()

    messages = [record.getMessage() for record in caplog.records]
    assert "Starting plugin hook: plugin=alpha stage=configure_image" in messages
    assert "Starting plugin hook: plugin=alpha stage=configure_container" in messages
    assert "Starting plugin hook: plugin=alpha stage=post_start" in messages


def test_build_and_run_finishes_all_plugin_hooks_before_returning(monkeypatch) -> None:
    events: list[tuple[str, str]] = []
    _stub_docker_calls(monkeypatch, events)

    builder = ContainerBuilderService(
        plugins=[
            _RecordingPlugin("alpha", events),
            _RecordingPlugin("beta", events),
        ]
    )
    builder._docker_client = _DockerClient()

    running = builder.build_and_run()

    assert running.name == "notes-assistant-opencode"
    assert events == [
        ("alpha", "configure_image"),
        ("beta", "configure_image"),
        ("alpha", "configure_container"),
        ("beta", "configure_container"),
        ("docker", "build_image"),
        ("docker", "ensure_named_volumes"),
        ("docker", "run_container"),
        ("alpha", "post_start"),
        ("beta", "post_start"),
    ]


def test_build_and_run_fails_fast_when_configure_hook_fails(monkeypatch) -> None:
    events: list[tuple[str, str]] = []
    _stub_docker_calls(monkeypatch, events)

    builder = ContainerBuilderService(
        plugins=[
            _RecordingPlugin("failing", events, fail_stage="configure_container"),
            _RecordingPlugin("later", events),
        ]
    )
    builder._docker_client = _DockerClient()

    with pytest.raises(
        RuntimeError,
        match="Plugin hook failed: plugin=failing stage=configure_container",
    ):
        builder.build_and_run()

    assert ("later", "configure_container") not in events
    assert ("docker", "build_image") not in events


def test_build_and_run_preserves_explicit_plugin_configuration_errors(monkeypatch) -> None:
    events: list[tuple[str, str]] = []
    _stub_docker_calls(monkeypatch, events)

    builder = ContainerBuilderService(
        plugins=[_ConfigurationErrorPlugin("failing", "configure_container")]
    )
    builder._docker_client = _DockerClient()

    with pytest.raises(ConfigurationError, match="explicit plugin failure"):
        builder.build_and_run()

    assert ("docker", "build_image") not in events


def test_build_and_run_fails_fast_when_post_start_hook_fails(monkeypatch) -> None:
    events: list[tuple[str, str]] = []
    _stub_docker_calls(monkeypatch, events)

    builder = ContainerBuilderService(
        plugins=[
            _RecordingPlugin("failing", events, fail_stage="post_start"),
            _RecordingPlugin("later", events),
        ]
    )
    builder._docker_client = _DockerClient()

    with pytest.raises(
        RuntimeError,
        match="Plugin hook failed: plugin=failing stage=post_start",
    ):
        builder.build_and_run()

    assert ("docker", "run_container") in events
    assert ("later", "post_start") not in events


class _ConfigurationErrorPlugin:
    def __init__(self, name: str, fail_stage: str) -> None:
        self.name = name
        self._fail_stage = fail_stage

    def configure_image(self, _image: ImageSpec) -> None:
        self._fail_if_needed("configure_image")

    def configure_container(self, _container: ContainerSpec) -> None:
        self._fail_if_needed("configure_container")

    def post_start(self, _runtime: ContainerRuntimeContext) -> None:
        self._fail_if_needed("post_start")

    def _fail_if_needed(self, stage: str) -> None:
        if self._fail_stage == stage:
            raise ConfigurationError("explicit plugin failure")


def _stub_docker_calls(monkeypatch, events: list[tuple[str, str]]) -> None:
    def build_image(_docker_client: Any, _image_spec: ImageSpec, _image_tag: str) -> object:
        events.append(("docker", "build_image"))
        return object()

    def ensure_named_volumes(_docker_client: Any, _container_spec: ContainerSpec) -> None:
        events.append(("docker", "ensure_named_volumes"))

    def run_container(_docker_client: Any, _container_spec: ContainerSpec) -> _Container:
        events.append(("docker", "run_container"))
        return _Container()

    monkeypatch.setattr(container_builder_service, "build_image", build_image)
    monkeypatch.setattr(container_builder_service, "ensure_named_volumes", ensure_named_volumes)
    monkeypatch.setattr(container_builder_service, "run_container", run_container)
