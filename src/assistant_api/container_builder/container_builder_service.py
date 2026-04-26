from __future__ import annotations

from typing import Any

from assistant_api.container_builder.container_plugin import ContainerPluginService
from assistant_api.models import (
    ContainerRuntimeContext,
    ContainerSpec,
    ImageSpec,
    RunningContainer,
)

from ._docker_runtime import build_image, ensure_named_volumes, run_container


DEFAULT_IMAGE_TAG = "notes-assistant-opencode:latest"
DEFAULT_CONTAINER_NAME = "notes-assistant-opencode"


class ContainerBuilderService:
    def __init__(
        self,
        plugins: list[ContainerPluginService],
        container_name: str = DEFAULT_CONTAINER_NAME,
    ) -> None:
        self.plugins = plugins
        self.container_name = container_name
        self._docker_client: Any | None = None

    def build(self) -> None:
        image_spec, _container_spec = self._prepare_specs()
        build_image(self._client(), image_spec, DEFAULT_IMAGE_TAG)

    def build_and_run(self) -> RunningContainer:
        self.build()
        return self._run_started_container()

    def _run_started_container(self) -> RunningContainer:
        _image_spec, container_spec = self._prepare_specs()
        docker_client = self._client()
        self._replace_container_if_needed(docker_client, container_spec.name)
        ensure_named_volumes(docker_client, container_spec)

        container = run_container(docker_client, container_spec)
        runtime = ContainerRuntimeContext(
            docker_client=docker_client,
            container=container,
            state=container_spec.state,
        )
        for plugin in self.plugins:
            plugin.post_start(runtime)

        return RunningContainer(container=container, container_spec=container_spec)

    def stop(self, remove: bool = False) -> None:
        docker_client = self._client()
        container = docker_client.containers.get(self.container_name)
        container.stop()
        if remove:
            container.remove()

    def _prepare_specs(self) -> tuple[ImageSpec, ContainerSpec]:
        image_spec = ImageSpec(run_commands=["mkdir -p /workspace"])
        container_spec = ContainerSpec(
            name=self.container_name,
            image_tag=DEFAULT_IMAGE_TAG,
        )

        for plugin in self.plugins:
            plugin.configure_image(image_spec)
        for plugin in self.plugins:
            plugin.configure_container(container_spec)

        return image_spec, container_spec

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
        container.remove(force=True)
