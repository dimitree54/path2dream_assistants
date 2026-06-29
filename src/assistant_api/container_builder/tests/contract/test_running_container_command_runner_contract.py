from __future__ import annotations

import inspect
import threading
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

import pytest

from assistant_api.container_builder import (
    ContainerCommandError,
    ContainerCommandTimeoutError,
    RunningContainerCommandRunnerService,
)
from assistant_api.models import CommandExecResult, ContainerSpec, RunningContainer


def test_public_interface_is_exported_with_expected_signature() -> None:
    init_signature = inspect.signature(RunningContainerCommandRunnerService)
    run_signature = inspect.signature(RunningContainerCommandRunnerService.run_command)

    assert list(init_signature.parameters) == ["running_container"]
    assert list(run_signature.parameters) == [
        "self",
        "command",
        "working_dir",
        "timeout_seconds",
    ]
    assert run_signature.parameters["working_dir"].kind is inspect.Parameter.KEYWORD_ONLY
    assert run_signature.parameters["timeout_seconds"].kind is inspect.Parameter.KEYWORD_ONLY
    assert issubclass(ContainerCommandTimeoutError, ContainerCommandError)


def test_successful_command_executes_exact_vector_and_captures_combined_output() -> None:
    scenario = _ExecScenario(
        exit_code=0,
        output_chunks=[b"stdout line\n", b"stderr line\n"],
    )
    running, api, _container = _running_container([scenario])
    runner = RunningContainerCommandRunnerService(running)

    result = runner.run_command(
        ["opencode", "run", "literal $PROMPT"],
        working_dir=PurePosixPath("/workspace"),
        timeout_seconds=10,
    )

    assert isinstance(result, CommandExecResult)
    assert result.exit_code == 0
    assert result.output == "stdout line\nstderr line\n"
    assert api.create_calls == [
        {
            "container_id": "container-id",
            "command": ["opencode", "run", "literal $PROMPT"],
            "stdout": True,
            "stderr": True,
            "workdir": "/workspace",
        }
    ]


def test_nonzero_exit_returns_result_without_hiding_status() -> None:
    running, _api, _container = _running_container(
        [_ExecScenario(exit_code=17, output_chunks=[b"command failed\n"])]
    )
    runner = RunningContainerCommandRunnerService(running)

    result = runner.run_command(
        ["/usr/bin/test-command"],
        timeout_seconds=10,
    )

    assert result.exit_code == 17
    assert result.output == "command failed\n"


def test_missing_working_directory_fails_fast_as_command_error() -> None:
    running, api, _container = _running_container(
        [_ExecScenario(exit_code=0)],
        missing_workdirs={"/missing"},
    )
    runner = RunningContainerCommandRunnerService(running)

    with pytest.raises(ContainerCommandError, match="Container command failed to start"):
        runner.run_command(
            ["opencode", "run", "prompt"],
            working_dir=PurePosixPath("/missing"),
            timeout_seconds=10,
        )

    assert api.start_calls == []


def test_stopped_container_fails_before_exec_start() -> None:
    running, api, _container = _running_container(
        [_ExecScenario(exit_code=0)],
        status="exited",
    )
    runner = RunningContainerCommandRunnerService(running)

    with pytest.raises(ContainerCommandError, match="container is not running"):
        runner.run_command(["opencode", "run", "prompt"], timeout_seconds=10)

    assert api.create_calls == []


def test_timeout_terminates_command_and_exposes_output_tail() -> None:
    running, _api, container = _running_container(
        [
            _ExecScenario(
                exit_code=0,
                output_chunks=[b"before timeout\n"],
                block_until_killed=True,
            )
        ]
    )
    runner = RunningContainerCommandRunnerService(running)

    with pytest.raises(ContainerCommandTimeoutError) as error:
        runner.run_command(["opencode", "run", "slow prompt"], timeout_seconds=1)

    assert error.value.timeout_seconds == 1
    assert error.value.output_tail == "before timeout\n"
    assert ["kill", "-TERM", "-123"] in container.kill_commands


def test_invalid_runtime_arguments_fail_fast() -> None:
    running, _api, _container = _running_container([_ExecScenario(exit_code=0)])
    runner = RunningContainerCommandRunnerService(running)

    with pytest.raises(ContainerCommandError, match="command"):
        runner.run_command([], timeout_seconds=10)
    with pytest.raises(ContainerCommandError, match="timeout_seconds"):
        runner.run_command(["true"], timeout_seconds=0)
    with pytest.raises(ContainerCommandError, match="working_dir"):
        runner.run_command(
            ["true"],
            working_dir=PurePosixPath("relative"),
            timeout_seconds=10,
        )


def _running_container(
    scenarios: list[_ExecScenario],
    *,
    status: str = "running",
    missing_workdirs: set[str] | None = None,
) -> tuple[RunningContainer, _DockerApi, _Container]:
    api = _DockerApi(scenarios=scenarios, missing_workdirs=missing_workdirs or set())
    container = _Container(api=api, status=status)
    running = RunningContainer(
        container=container,
        container_spec=ContainerSpec(name="container-name", image_tag="image-tag"),
    )
    return running, api, container


@dataclass(slots=True)
class _ExecScenario:
    exit_code: int
    output_chunks: list[bytes] = field(default_factory=list)
    block_until_killed: bool = False


@dataclass(slots=True)
class _ExecRunResult:
    exit_code: int
    output: bytes


class _DockerClient:
    def __init__(self, api: _DockerApi) -> None:
        self.api = api


class _Container:
    id = "container-id"
    name = "container-name"

    def __init__(self, *, api: _DockerApi, status: str) -> None:
        self.client = _DockerClient(api)
        self.status = status
        self.kill_commands: list[list[str]] = []

    def reload(self) -> None:
        return None

    def exec_run(self, command: list[str]) -> _ExecRunResult:
        if len(command) == 3 and command[0] == "kill" and command[1] in {"-TERM", "-KILL"}:
            self.kill_commands.append(command)
            self.client.api.signal(command[1], command[2])
            return _ExecRunResult(exit_code=0, output=b"")
        return _ExecRunResult(exit_code=127, output=b"unknown command")


class _DockerApi:
    def __init__(
        self,
        *,
        scenarios: list[_ExecScenario],
        missing_workdirs: set[str],
    ) -> None:
        self._scenarios = scenarios
        self._missing_workdirs = missing_workdirs
        self._execs: dict[str, _ExecRecord] = {}
        self.create_calls: list[dict[str, object]] = []
        self.start_calls: list[str] = []

    def exec_create(
        self,
        container_id: str,
        command: list[str],
        *,
        stdout: bool,
        stderr: bool,
        workdir: str | None,
    ) -> dict[str, str]:
        self.create_calls.append(
            {
                "container_id": container_id,
                "command": command,
                "stdout": stdout,
                "stderr": stderr,
                "workdir": workdir,
            }
        )
        if workdir in self._missing_workdirs:
            raise RuntimeError(f"working directory does not exist: {workdir}")
        if not self._scenarios:
            raise RuntimeError("no exec scenario configured")
        exec_id = f"exec-{len(self._execs)}"
        self._execs[exec_id] = _ExecRecord(
            scenario=self._scenarios.pop(0),
            pid=123 + len(self._execs),
        )
        return {"Id": exec_id}

    def exec_start(
        self,
        exec_id: str,
        *,
        stream: bool,
        demux: bool,
    ) -> Any:
        assert stream is True
        assert demux is False
        self.start_calls.append(exec_id)
        return self._execs[exec_id].stream()

    def exec_inspect(self, exec_id: str) -> dict[str, object]:
        record = self._execs[exec_id]
        return {
            "ExitCode": record.scenario.exit_code if record.finished.is_set() else None,
            "Running": not record.finished.is_set(),
            "Pid": record.pid,
        }

    def signal(self, _signal: str, target: str) -> None:
        for record in self._execs.values():
            if target in {str(record.pid), f"-{record.pid}"}:
                record.killed.set()


class _ExecRecord:
    def __init__(self, *, scenario: _ExecScenario, pid: int) -> None:
        self.scenario = scenario
        self.pid = pid
        self.killed = threading.Event()
        self.finished = threading.Event()

    def stream(self) -> Any:
        try:
            for chunk in self.scenario.output_chunks:
                yield chunk
            if self.scenario.block_until_killed:
                self.killed.wait()
        finally:
            self.finished.set()
