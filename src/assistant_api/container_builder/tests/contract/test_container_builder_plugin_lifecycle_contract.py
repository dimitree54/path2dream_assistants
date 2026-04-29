from __future__ import annotations

import logging
from typing import Any

import pytest

import assistant_api.container_builder.container_builder_service as container_builder_service
from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.models import (
    ContainerRuntimeContext,
    ContainerSpec,
    ContainerStartupTask,
    ImageSpec,
)


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


def test_prepare_specs_assigns_startup_tasks_to_registering_plugin() -> None:
    events: list[tuple[str, str]] = []

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[_StartupTaskPlugin("installer", events)]
    )._prepare_specs()

    assert len(container_spec.startup_tasks) == 1
    assert container_spec.startup_tasks[0].owner_plugin_name == "installer"


def test_build_and_run_waits_for_startup_tasks_before_post_start(monkeypatch) -> None:
    events: list[tuple[str, str]] = []
    container = _StartupStatusContainer(
        statuses=[
            "status=running\nowner=installer\nname=install\nexit_code=\n",
            "status=succeeded\nowner=installer\nname=install\nexit_code=0\n",
        ]
    )
    _stub_docker_calls(monkeypatch, events, container=container)
    monkeypatch.setattr(container_builder_service, "STARTUP_TASK_POLL_SECONDS", 0)

    builder = ContainerBuilderService(
        plugins=[
            _StartupTaskPlugin("installer", events),
            _RecordingPlugin("later", events),
        ]
    )
    builder._docker_client = _DockerClient()

    builder.build_and_run()

    assert container.status_reads == 2
    assert ("later", "post_start") in events


def test_build_and_run_attributes_startup_task_failure_to_owner(monkeypatch) -> None:
    events: list[tuple[str, str]] = []
    container = _StartupStatusContainer(
        statuses=["status=failed\nowner=installer\nname=install\nexit_code=7\n"],
        log_output="installer exploded",
    )
    _stub_docker_calls(monkeypatch, events, container=container)

    builder = ContainerBuilderService(
        plugins=[
            _StartupTaskPlugin("installer", events),
            _RecordingPlugin("later", events),
        ]
    )
    builder._docker_client = _DockerClient()

    with pytest.raises(
        RuntimeError,
        match="plugin=installer task=install exit_code=7",
    ) as error:
        builder.build_and_run()

    assert "installer exploded" in str(error.value)
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


class _StartupTaskPlugin:
    def __init__(self, name: str, events: list[tuple[str, str]]) -> None:
        self.name = name
        self._events = events

    def configure_image(self, _image: ImageSpec) -> None:
        self._events.append((self.name, "configure_image"))

    def configure_container(self, container: ContainerSpec) -> None:
        self._events.append((self.name, "configure_container"))
        container.startup_tasks.append(
            ContainerStartupTask(name="install", command=["/bin/sh", "-lc", "true"])
        )

    def post_start(self, _runtime: ContainerRuntimeContext) -> None:
        self._events.append((self.name, "post_start"))


class _ExecRunResult:
    def __init__(self, exit_code: int, output: str) -> None:
        self.exit_code = exit_code
        self.output = output.encode("utf-8")


class _StartupStatusContainer(_Container):
    def __init__(self, statuses: list[str], log_output: str = "") -> None:
        self._statuses = statuses
        self._log_output = log_output
        self.status_reads = 0
        self.status = "running"

    def exec_run(self, command: list[str]) -> _ExecRunResult:
        command_text = " ".join(command)
        if ".status.log" in command_text:
            return _ExecRunResult(0, self._log_output)
        if ".status" in command_text:
            status = self._statuses[min(self.status_reads, len(self._statuses) - 1)]
            self.status_reads += 1
            return _ExecRunResult(0, status)
        return _ExecRunResult(0, "")

    def reload(self) -> None:
        return None

    def logs(self, tail: int = 200) -> bytes:
        return self._log_output.encode("utf-8")


def _stub_docker_calls(
    monkeypatch,
    events: list[tuple[str, str]],
    *,
    container: _Container | None = None,
) -> None:
    def build_image(_docker_client: Any, _image_spec: ImageSpec, _image_tag: str) -> object:
        events.append(("docker", "build_image"))
        return object()

    def ensure_named_volumes(_docker_client: Any, _container_spec: ContainerSpec) -> None:
        events.append(("docker", "ensure_named_volumes"))

    def run_container(_docker_client: Any, _container_spec: ContainerSpec) -> _Container:
        events.append(("docker", "run_container"))
        return container or _Container()

    monkeypatch.setattr(container_builder_service, "build_image", build_image)
    monkeypatch.setattr(container_builder_service, "ensure_named_volumes", ensure_named_volumes)
    monkeypatch.setattr(container_builder_service, "run_container", run_container)
