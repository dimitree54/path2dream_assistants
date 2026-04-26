from __future__ import annotations

from typing import get_type_hints

from assistant_api.container_builder.container_plugin import (
    MOUNT_METADATA_STATE_KEY,
    ContainerPluginService,
)
from assistant_api.models import ContainerRuntimeContext, ContainerSpec, ImageSpec


def test_container_plugin_service_defines_lifecycle_contract() -> None:
    assert hasattr(ContainerPluginService, "configure_image")
    assert hasattr(ContainerPluginService, "configure_container")
    assert hasattr(ContainerPluginService, "post_start")
    assert MOUNT_METADATA_STATE_KEY == "mount"


def test_container_plugin_lifecycle_uses_public_models() -> None:
    image_hints = get_type_hints(ContainerPluginService.configure_image)
    container_hints = get_type_hints(ContainerPluginService.configure_container)
    post_start_hints = get_type_hints(ContainerPluginService.post_start)

    assert image_hints["image"] is ImageSpec
    assert container_hints["container"] is ContainerSpec
    assert post_start_hints["runtime"] is ContainerRuntimeContext
