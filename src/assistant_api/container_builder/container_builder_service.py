from __future__ import annotations

import logging
import shlex
import time
from dataclasses import replace
from typing import Any, Literal, cast

from assistant_api.container_builder.container_plugin import ContainerPluginService
from assistant_api.models import (
    CommandExecResult,
    ContainerExecutionIdentity,
    ContainerStartupTask,
    ContainerRuntimeContext,
    ContainerSpec,
    ImageSpec,
    RunningContainer,
)

from ._errors import ConfigurationError
from ._execution_identity import validate_execution_identity_mounts
from ._plugin_lifecycle import PluginLifecycle
from ._docker_runtime import (
    build_image,
    ensure_named_volumes,
    image_exists,
    run_container,
    startup_task_log_path,
    startup_task_status_path,
)


BuildPolicy = Literal["always", "if_missing", "never"]
DEFAULT_IMAGE_TAG = "notes-assistant-opencode:latest"
DEFAULT_CONTAINER_NAME = "notes-assistant-opencode"
STARTUP_TASK_TIMEOUT_SECONDS = 300
STARTUP_TASK_POLL_SECONDS = 0.5
LOGGER = logging.getLogger(__name__)


class ContainerBuilderService:
    def __init__(
        self,
        plugins: list[ContainerPluginService],
        container_name: str = DEFAULT_CONTAINER_NAME,
        *,
        image_tag: str = DEFAULT_IMAGE_TAG,
        build_policy: BuildPolicy = "always",
        execution_identity: ContainerExecutionIdentity | None = None,
    ) -> None:
        self.plugins = plugins
        self.container_name = container_name
        self.image_tag = image_tag
        self.build_policy = _validate_build_policy(build_policy)
        if execution_identity is not None and not isinstance(
            execution_identity, ContainerExecutionIdentity
        ):
            raise ConfigurationError(
                "execution_identity must be a ContainerExecutionIdentity"
            )
        self.execution_identity = execution_identity
        self._docker_client: Any | None = None

    def build(self) -> None:
        lifecycle = PluginLifecycle()
        image_spec, _container_spec = self._prepare_specs(lifecycle)
        lifecycle.validate_finished()
        self._resolve_image(self._client(), image_spec)

    def build_and_run(self) -> RunningContainer:
        lifecycle = PluginLifecycle()
        image_spec, container_spec = self._prepare_specs(lifecycle)
        lifecycle.validate_finished()
        self._resolve_image(self._client(), image_spec)
        return self._run_started_container(container_spec, lifecycle)

    def _run_started_container(
        self,
        container_spec: ContainerSpec,
        lifecycle: PluginLifecycle,
    ) -> RunningContainer:
        docker_client = self._client()
        self._replace_container_if_needed(docker_client, container_spec.name)
        ensure_named_volumes(docker_client, container_spec)

        container = run_container(docker_client, container_spec)
        runtime = ContainerRuntimeContext(
            docker_client=docker_client,
            container=container,
            state=container_spec.state,
            execution_identity=container_spec.execution_identity,
        )
        self._wait_for_startup_tasks(container, container_spec.startup_tasks)
        for index, plugin in enumerate(self.plugins):
            lifecycle.run(
                index,
                plugin,
                "post_start",
                lambda plugin=plugin: plugin.post_start(runtime),
            )
        lifecycle.validate_finished()

        return RunningContainer(container=container, container_spec=container_spec)

    def stop(self, remove: bool = False) -> None:
        docker_client = self._client()
        container = docker_client.containers.get(self.container_name)
        container.stop()
        if remove:
            container.remove()

    def _prepare_specs(
        self,
        lifecycle: PluginLifecycle | None = None,
    ) -> tuple[ImageSpec, ContainerSpec]:
        if lifecycle is None:
            lifecycle = PluginLifecycle()
        image_spec = ImageSpec(run_commands=["mkdir -p /workspace"])
        container_spec = ContainerSpec(
            name=self.container_name,
            image_tag=self.image_tag,
            execution_identity=self.execution_identity,
        )

        for index, plugin in enumerate(self.plugins):
            lifecycle.run(
                index,
                plugin,
                "configure_image",
                lambda plugin=plugin: plugin.configure_image(image_spec),
            )
        for index, plugin in enumerate(self.plugins):
            task_count_before = len(container_spec.startup_tasks)
            identity_before = container_spec.execution_identity
            lifecycle.run(
                index,
                plugin,
                "configure_container",
                lambda plugin=plugin: plugin.configure_container(container_spec),
            )
            identity_after = container_spec.execution_identity
            if identity_after is not None and not isinstance(
                identity_after, ContainerExecutionIdentity
            ):
                raise ConfigurationError(
                    "plugin contributed an invalid container execution identity"
                )
            if identity_after != identity_before:
                if identity_before is not None:
                    raise ConfigurationError(
                        "conflicting container execution identity contributions"
                    )
                if identity_after is not None:
                    container_spec.require_execution_identity(identity_after)
            self._assign_startup_task_owners(
                container_spec,
                task_count_before,
                plugin.name,
            )
            if len(container_spec.execution_identity_requirements()) > 1:
                raise ConfigurationError(
                    "conflicting container execution identity contributions"
                )
        lifecycle.validate_finished()
        validate_execution_identity_mounts(container_spec)

        return image_spec, container_spec

    def _resolve_image(self, docker_client: Any, image_spec: ImageSpec) -> None:
        if self.build_policy == "always":
            self._build_image(docker_client, image_spec)
            return

        if image_exists(docker_client, self.image_tag):
            LOGGER.info(
                "Docker image reused: tag=%s policy=%s",
                self.image_tag,
                self.build_policy,
            )
            return

        if self.build_policy == "if_missing":
            self._build_image(docker_client, image_spec)
            return

        LOGGER.info(
            "Docker image rejected: tag=%s policy=%s reason=missing",
            self.image_tag,
            self.build_policy,
        )
        raise ConfigurationError(
            "Docker image is missing for build_policy='never': "
            f"{self.image_tag}"
        )

    def _build_image(self, docker_client: Any, image_spec: ImageSpec) -> None:
        build_image(docker_client, image_spec, self.image_tag)
        LOGGER.info(
            "Docker image built: tag=%s policy=%s",
            self.image_tag,
            self.build_policy,
        )

    def _client(self) -> Any:
        if self._docker_client is None:
            import docker

            self._docker_client = docker.from_env()
        return self._docker_client

    def _replace_container_if_needed(self, docker_client: Any, container_name: str) -> None:
        try:
            container = docker_client.containers.get(container_name)
        except Exception:
            return
        LOGGER.info("Removing existing container before start: name=%s", container_name)
        container.remove(force=True)

    @staticmethod
    def _assign_startup_task_owners(
        container_spec: ContainerSpec,
        first_new_task_index: int,
        plugin_name: str,
    ) -> None:
        for index in range(first_new_task_index, len(container_spec.startup_tasks)):
            task = container_spec.startup_tasks[index]
            if task.owner_plugin_name is None:
                container_spec.startup_tasks[index] = replace(
                    task,
                    owner_plugin_name=plugin_name,
                )

    def _wait_for_startup_tasks(
        self,
        container: Any,
        tasks: list[ContainerStartupTask],
    ) -> None:
        if not tasks:
            return

        deadline = time.monotonic() + STARTUP_TASK_TIMEOUT_SECONDS
        succeeded: set[int] = set()
        while time.monotonic() < deadline:
            for index, task in enumerate(tasks):
                if index in succeeded:
                    continue
                status = self._read_startup_task_status(container, index, task)
                if not status:
                    continue
                if status.get("status") == "succeeded":
                    succeeded.add(index)
                    continue
                if status.get("status") == "failed":
                    raise RuntimeError(
                        self._startup_task_failure_message(
                            container,
                            index,
                            task,
                            status,
                        )
                    )
            if len(succeeded) == len(tasks):
                return
            if self._container_has_stopped(container):
                raise RuntimeError(
                    self._stopped_container_startup_message(
                        container,
                        tasks,
                        succeeded,
                    )
                )
            time.sleep(STARTUP_TASK_POLL_SECONDS)

        task = next(task for index, task in enumerate(tasks) if index not in succeeded)
        raise TimeoutError(
            "Startup task did not finish before timeout: "
            f"plugin={task.owner_plugin_name or 'unknown'} "
            f"task={task.name} timeout_seconds={STARTUP_TASK_TIMEOUT_SECONDS}"
        )

    def _read_startup_task_status(
        self,
        container: Any,
        index: int,
        task: ContainerStartupTask,
    ) -> dict[str, str] | None:
        result = self._exec_container(
            container,
            [
                "/bin/sh",
                "-lc",
                f"cat {shlex.quote(startup_task_status_path(index, task))}",
            ],
        )
        if result is None or result.exit_code != 0:
            return None
        return _parse_startup_status(result.output)

    def _startup_task_failure_message(
        self,
        container: Any,
        index: int,
        task: ContainerStartupTask,
        status: dict[str, str],
    ) -> str:
        log_output = self._read_container_text_file(
            container,
            startup_task_log_path(index, task),
        )
        return (
            "Plugin startup task failed: "
            f"plugin={status.get('owner') or task.owner_plugin_name or 'unknown'} "
            f"task={status.get('name') or task.name} "
            f"exit_code={status.get('exit_code') or 'unknown'}"
            f"{_format_output_tail(log_output)}"
        )

    def _stopped_container_startup_message(
        self,
        container: Any,
        tasks: list[ContainerStartupTask],
        succeeded: set[int],
    ) -> str:
        task = next(task for index, task in enumerate(tasks) if index not in succeeded)
        return (
            "Container exited before startup tasks completed: "
            f"plugin={task.owner_plugin_name or 'unknown'} task={task.name}"
            f"{_format_output_tail(self._container_logs(container))}"
        )

    def _read_container_text_file(self, container: Any, path: str) -> str:
        result = self._exec_container(
            container,
            ["/bin/sh", "-lc", f"cat {shlex.quote(path)}"],
        )
        if result is None or result.exit_code != 0:
            return ""
        return result.output

    @staticmethod
    def _exec_container(container: Any, command: list[str]) -> CommandExecResult | None:
        try:
            result = container.exec_run(command)
        except Exception:
            return None
        output = result.output
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return CommandExecResult(exit_code=result.exit_code, output=output)

    @staticmethod
    def _container_has_stopped(container: Any) -> bool:
        try:
            container.reload()
        except Exception:
            pass
        status = getattr(container, "status", None)
        return status in {"dead", "exited", "removing"}

    @staticmethod
    def _container_logs(container: Any) -> str:
        try:
            logs = container.logs(tail=200)
        except Exception:
            return ""
        if isinstance(logs, bytes):
            return logs.decode("utf-8", errors="replace")
        return str(logs)


def _parse_startup_status(output: str) -> dict[str, str]:
    status: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        status[key] = value
    return status


def _format_output_tail(output: str) -> str:
    if not output:
        return ""
    return "\n--- output tail ---\n" + output[-4000:]


def _validate_build_policy(build_policy: str) -> BuildPolicy:
    if build_policy in ("always", "if_missing", "never"):
        return cast(BuildPolicy, build_policy)
    raise ConfigurationError("build_policy must be one of: always, if_missing, never")
