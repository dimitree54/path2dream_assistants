from __future__ import annotations

from pathlib import PurePosixPath

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin import OPENCODE_RUNTIME_STATE_KEY
from assistant_api.container_builder.container_plugin.opencode_server_plugin import (
    OpenCodeServerPluginService,
)
from assistant_api.models import ContainerRuntimeContext, ImageSpec, OpenCodeRuntimeMetadata


def test_opencode_server_plugin_adds_command_and_port_without_persistence() -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[OpenCodeServerPluginService(host_port=4097)]
    )._prepare_specs()

    assert container_spec.command == [
        "opencode",
        "serve",
        "--hostname",
        "0.0.0.0",
        "--port",
        "4096",
    ]
    assert container_spec.ports == {4096: 4097}
    assert container_spec.env == {}
    assert container_spec.volumes == {}
    assert container_spec.working_dir == PurePosixPath("/workspace")
    runtime = container_spec.state[OPENCODE_RUNTIME_STATE_KEY]
    assert isinstance(runtime, OpenCodeRuntimeMetadata)
    assert runtime.working_dir == PurePosixPath("/workspace")
    assert runtime.api_container_port == 4096


def test_opencode_server_plugin_uses_configured_port_and_existing_working_dir() -> None:
    plugin = OpenCodeServerPluginService(host_port=5097, container_port=5096)
    _image_spec, container_spec = ContainerBuilderService(plugins=[])._prepare_specs()
    container_spec.working_dir = PurePosixPath("/workspace/project")

    plugin.configure_container(container_spec)

    assert container_spec.command == [
        "opencode",
        "serve",
        "--hostname",
        "0.0.0.0",
        "--port",
        "5096",
    ]
    assert container_spec.ports == {5096: 5097}
    runtime = container_spec.state[OPENCODE_RUNTIME_STATE_KEY]
    assert isinstance(runtime, OpenCodeRuntimeMetadata)
    assert runtime.working_dir == PurePosixPath("/workspace/project")
    assert runtime.api_container_port == 5096


def test_opencode_server_plugin_does_not_configure_image_or_post_start() -> None:
    plugin = OpenCodeServerPluginService(host_port=4097)
    image_spec = ImageSpec()

    assert plugin.configure_image(image_spec) is None
    assert image_spec.run_commands == []
    assert (
        plugin.post_start(
            ContainerRuntimeContext(docker_client=object(), container=object(), state={})
        )
        is None
    )
