from __future__ import annotations

import threading
from contextlib import suppress
from pathlib import PurePosixPath
from typing import Any

from assistant_api.models import CommandExecResult, RunningContainer


OUTPUT_TAIL_CHARS = 4000
TERMINATE_GRACE_SECONDS = 2.0
KILL_GRACE_SECONDS = 2.0


class ContainerCommandError(RuntimeError):
    """Raised when a container command cannot be started or inspected."""


class ContainerCommandTimeoutError(ContainerCommandError):
    def __init__(
        self,
        *,
        command: list[str],
        timeout_seconds: int,
        output_tail: str,
        termination_details: list[str],
    ) -> None:
        self.command = list(command)
        self.timeout_seconds = timeout_seconds
        self.output_tail = output_tail
        self.termination_details = list(termination_details)

        message = (
            "Container command timed out after "
            f"{timeout_seconds} seconds: {command[0]}"
        )
        if output_tail:
            message += "\n--- output tail ---\n" + output_tail
        if termination_details:
            message += "\n--- termination details ---\n" + "\n".join(
                termination_details
            )
        super().__init__(message)


class RunningContainerCommandRunnerService:
    def __init__(self, running_container: RunningContainer) -> None:
        self.running_container = running_container

    def run_command(
        self,
        command: list[str],
        *,
        working_dir: PurePosixPath | None = None,
        timeout_seconds: int,
    ) -> CommandExecResult:
        _validate_command(command)
        _validate_timeout(timeout_seconds)
        workdir = _validate_working_dir(working_dir)

        self._ensure_container_running()
        exec_id = self._create_exec(command, workdir)
        return self._start_and_wait(exec_id, command, timeout_seconds)

    def _ensure_container_running(self) -> None:
        container = self.running_container.container
        try:
            container.reload()
        except Exception as error:
            raise ContainerCommandError("Container status could not be reloaded") from error

        status = getattr(container, "status", None)
        if status != "running":
            raise ContainerCommandError(
                f"container is not running before command start: status={status!r}"
            )

    def _create_exec(self, command: list[str], workdir: str | None) -> str:
        identity = self.running_container.container_spec.execution_identity
        exec_command = identity.wrap_command(command) if identity else command
        try:
            response = self._docker_api().exec_create(
                self.running_container.id,
                exec_command,
                stdout=True,
                stderr=True,
                workdir=workdir,
                **({"user": identity.docker_user} if identity else {}),
            )
        except Exception as error:
            raise ContainerCommandError("Container command failed to start") from error

        exec_id = response.get("Id") if isinstance(response, dict) else None
        if not isinstance(exec_id, str) or not exec_id:
            raise ContainerCommandError("Container command failed to start")
        return exec_id

    def _start_and_wait(
        self,
        exec_id: str,
        command: list[str],
        timeout_seconds: int,
    ) -> CommandExecResult:
        output = _OutputBuffer()
        finished = threading.Event()
        stream_holder: list[Any] = []
        reader_errors: list[BaseException] = []

        thread = threading.Thread(
            target=self._read_exec_output,
            args=(exec_id, output, finished, stream_holder, reader_errors),
            daemon=True,
        )
        thread.start()

        if finished.wait(timeout_seconds):
            thread.join()
            self._raise_reader_error_if_needed(reader_errors)
            return self._exec_result(exec_id, output.text())

        termination_details = self._terminate_exec(exec_id, finished)
        if not finished.is_set():
            _close_streams(stream_holder)
            finished.wait(KILL_GRACE_SECONDS)
        raise ContainerCommandTimeoutError(
            command=command,
            timeout_seconds=timeout_seconds,
            output_tail=_output_tail(output.text()),
            termination_details=termination_details,
        )

    def _read_exec_output(
        self,
        exec_id: str,
        output: _OutputBuffer,
        finished: threading.Event,
        stream_holder: list[Any],
        reader_errors: list[BaseException],
    ) -> None:
        try:
            stream = self._docker_api().exec_start(
                exec_id,
                stream=True,
                demux=False,
            )
            stream_holder.append(stream)
            for chunk in stream:
                output.append(chunk)
        except Exception as error:
            reader_errors.append(error)
        finally:
            _close_streams(stream_holder)
            finished.set()

    def _terminate_exec(
        self,
        exec_id: str,
        finished: threading.Event,
    ) -> list[str]:
        details: list[str] = []
        pid = self._exec_pid(exec_id)
        if pid is None:
            return ["Docker exec pid is unavailable; command could not be signaled"]

        details.extend(self._send_signal("TERM", pid))
        if finished.wait(TERMINATE_GRACE_SECONDS):
            return details

        details.extend(self._send_signal("KILL", pid))
        finished.wait(KILL_GRACE_SECONDS)
        return details

    def _exec_result(self, exec_id: str, output: str) -> CommandExecResult:
        inspection = self._inspect_exec(exec_id)
        exit_code = inspection.get("ExitCode")
        if not isinstance(exit_code, int):
            raise ContainerCommandError("Container command exit code is unavailable")
        return CommandExecResult(exit_code=exit_code, output=output)

    def _exec_pid(self, exec_id: str) -> int | None:
        inspection = self._inspect_exec(exec_id)
        pid = inspection.get("Pid")
        if isinstance(pid, int) and pid > 0:
            return pid
        return None

    def _inspect_exec(self, exec_id: str) -> dict[str, Any]:
        try:
            inspection = self._docker_api().exec_inspect(exec_id)
        except Exception as error:
            raise ContainerCommandError("Container command inspection failed") from error
        if not isinstance(inspection, dict):
            raise ContainerCommandError("Container command inspection failed")
        return inspection

    def _send_signal(self, signal_name: str, pid: int) -> list[str]:
        details: list[str] = []
        for target in (f"-{pid}", str(pid)):
            command = ["kill", f"-{signal_name}", target]
            try:
                identity = self.running_container.container_spec.execution_identity
                result = self.running_container.container.exec_run(
                    command,
                    **({"user": identity.docker_user} if identity else {}),
                )
            except Exception as error:
                details.append(f"{' '.join(command)} failed: {error}")
                continue

            output = _decode_output(getattr(result, "output", b""))
            exit_code = getattr(result, "exit_code", None)
            if exit_code == 0:
                return details
            details.append(
                f"{' '.join(command)} exited with {exit_code}"
                f"{_format_signal_output(output)}"
            )
        return details

    def _raise_reader_error_if_needed(
        self,
        reader_errors: list[BaseException],
    ) -> None:
        if not reader_errors:
            return
        raise ContainerCommandError("Container command output read failed") from reader_errors[0]

    def _docker_api(self) -> Any:
        return self.running_container.container.client.api


class _OutputBuffer:
    def __init__(self) -> None:
        self._chunks: list[str] = []
        self._lock = threading.Lock()

    def append(self, chunk: object) -> None:
        text = _decode_output(chunk)
        with self._lock:
            self._chunks.append(text)

    def text(self) -> str:
        with self._lock:
            return "".join(self._chunks)


def _validate_command(command: list[str]) -> None:
    if (
        not isinstance(command, list)
        or not command
        or command[0] == ""
        or any(not isinstance(argument, str) for argument in command)
    ):
        raise ContainerCommandError("command must be a non-empty list[str]")


def _validate_timeout(timeout_seconds: int) -> None:
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or timeout_seconds <= 0
    ):
        raise ContainerCommandError("timeout_seconds must be positive")


def _validate_working_dir(working_dir: PurePosixPath | None) -> str | None:
    if working_dir is None:
        return None
    if not isinstance(working_dir, PurePosixPath) or not working_dir.is_absolute():
        raise ContainerCommandError("working_dir must be an absolute PurePosixPath")
    return working_dir.as_posix()


def _decode_output(output: object) -> str:
    if isinstance(output, tuple):
        return "".join(_decode_output(part) for part in output if part is not None)
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    if isinstance(output, str):
        return output
    return str(output)


def _close_streams(streams: list[Any]) -> None:
    for stream in streams:
        close = getattr(stream, "close", None)
        if callable(close):
            with suppress(Exception):
                close()
        response = getattr(stream, "_response", None)
        if response is not None:
            with suppress(Exception):
                response.close()


def _output_tail(output: str) -> str:
    return output[-OUTPUT_TAIL_CHARS:]


def _format_signal_output(output: str) -> str:
    if not output:
        return ""
    return ": " + _output_tail(output)
