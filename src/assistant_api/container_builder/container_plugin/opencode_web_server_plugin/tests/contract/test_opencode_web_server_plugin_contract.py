from __future__ import annotations

from pathlib import PurePosixPath

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin.opencode_web_server_plugin import (
    OpenCodeWebServerPluginService,
)


def test_opencode_web_server_plugin_adds_command_and_port_without_persistence() -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[OpenCodeWebServerPluginService(host_port=4097)]
    )._prepare_specs()

    assert container_spec.command == [
        "opencode",
        "web",
        "--hostname",
        "0.0.0.0",
        "--port",
        "4096",
    ]
    assert container_spec.ports == {4096: 4097}
    assert container_spec.env == {}
    assert container_spec.volumes == {}
    assert container_spec.working_dir == PurePosixPath("/workspace")
