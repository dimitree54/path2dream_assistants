from __future__ import annotations

import re
from collections.abc import Mapping

from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.models import ContainerRuntimeContext, ContainerSpec, ImageSpec


ENV_VAR_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ContainerEnvironmentPluginService:
    name = "container-environment"

    def __init__(self, environment: Mapping[str, str]) -> None:
        if not isinstance(environment, Mapping):
            raise ConfigurationError("environment must be a mapping")

        copied_environment: list[tuple[str, str]] = []
        for name, value in environment.items():
            if not isinstance(name, str) or not ENV_VAR_PATTERN.fullmatch(name):
                raise ConfigurationError(
                    "environment contains an invalid environment variable name"
                )
            if not isinstance(value, str):
                raise ConfigurationError(
                    f"environment variable {name} value must be a string"
                )
            copied_environment.append((name, value))

        self._environment = tuple(copied_environment)

    def configure_image(self, image: ImageSpec) -> None:
        return None

    def configure_container(self, container: ContainerSpec) -> None:
        for name, value in self._environment:
            if name in container.env and container.env[name] != value:
                raise ConfigurationError(
                    f"container environment variable {name} conflicts with an existing value"
                )
        container.env.update(self._environment)

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        return None
