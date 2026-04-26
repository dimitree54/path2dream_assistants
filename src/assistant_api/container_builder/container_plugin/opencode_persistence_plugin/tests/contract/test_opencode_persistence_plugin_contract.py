from __future__ import annotations

from pathlib import PurePosixPath

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin.opencode_persistence_plugin import (
    OpenCodePersistencePluginService,
)


def test_opencode_persistence_plugin_adds_only_env_and_named_volumes() -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[OpenCodePersistencePluginService()]
    )._prepare_specs()

    assert container_spec.env == {
        "HOME": "/tmp/opencode-home",
        "XDG_CONFIG_HOME": "/tmp/opencode-home/.config",
        "XDG_DATA_HOME": "/tmp/opencode-home/.local/share",
    }
    assert container_spec.volumes["notes_assistant_api_opencode_config"].target == PurePosixPath(
        "/tmp/opencode-home/.config/opencode"
    )
    assert container_spec.volumes["notes_assistant_api_opencode_data"].target == PurePosixPath(
        "/tmp/opencode-home/.local/share/opencode"
    )
    assert container_spec.command is None
    assert container_spec.ports == {}
