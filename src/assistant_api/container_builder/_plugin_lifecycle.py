from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from assistant_api.container_builder.container_plugin import ContainerPluginService

from ._errors import ConfigurationError


LOGGER = logging.getLogger("assistant_api.container_builder.container_builder_service")


@dataclass(frozen=True, slots=True)
class PluginHook:
    plugin_index: int
    plugin_name: str
    stage: str


class PluginLifecycle:
    def __init__(self) -> None:
        self.started: list[PluginHook] = []
        self.finished: list[PluginHook] = []

    def run(
        self,
        plugin_index: int,
        plugin: ContainerPluginService,
        stage: str,
        hook: Callable[[], None],
    ) -> None:
        plugin_hook = PluginHook(plugin_index, plugin.name, stage)
        LOGGER.info("Starting plugin hook: plugin=%s stage=%s", plugin.name, stage)
        self.started.append(plugin_hook)
        try:
            hook()
        except ConfigurationError:
            raise
        except Exception as error:
            raise RuntimeError(
                f"Plugin hook failed: plugin={plugin.name} stage={stage}"
            ) from error
        self.finished.append(plugin_hook)

    def validate_finished(self) -> None:
        if self.started == self.finished:
            return
        missing = [hook for hook in self.started if hook not in self.finished]
        details = ", ".join(
            f"plugin={hook.plugin_name} stage={hook.stage}" for hook in missing
        )
        raise RuntimeError(f"Plugin hooks did not finish successfully: {details}")
