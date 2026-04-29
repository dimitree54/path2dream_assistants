from __future__ import annotations

import inspect
from pathlib import PurePosixPath

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.container_builder.container_plugin import MOUNT_METADATA_STATE_KEY
from assistant_api.models import ContainerRuntimeContext, ContainerSpec, ImageSpec
from inbox_upload_contract_helpers import (
    FakeContainer,
    mount_metadata,
    service_class,
    unused_port,
)


# ---------------------------------------------------------------------------
# Init-time contract
# ---------------------------------------------------------------------------


def test_public_service_class_name_and_init_signature_defaults() -> None:
    service = service_class()
    signature = inspect.signature(service)

    assert service.__name__ == "InboxUploadPluginService"
    assert list(signature.parameters) == [
        "host_port",
        "container_port",
        "upload_endpoint_path",
    ]
    assert signature.parameters["host_port"].default == 8090
    assert signature.parameters["container_port"].default is None
    assert signature.parameters["upload_endpoint_path"].default == "/api/inbox/upload"


def test_service_implements_container_plugin_protocol() -> None:
    plugin = service_class()(host_port=unused_port())

    assert hasattr(plugin, "name")
    assert isinstance(plugin.name, str)
    assert callable(plugin.configure_image)
    assert callable(plugin.configure_container)
    assert callable(plugin.post_start)


@pytest.mark.parametrize(
    ("kwargs", "expected_message"),
    [
        ({"host_port": 0}, "host_port"),
        ({"host_port": -1}, "host_port"),
        ({"host_port": 65536}, "host_port"),
        ({"host_port": "not-an-int"}, "host_port"),
        ({"container_port": 0}, "container_port"),
        ({"container_port": -1}, "container_port"),
        ({"container_port": 65536}, "container_port"),
        ({"container_port": "not-an-int"}, "container_port"),
    ],
)
def test_init_rejects_invalid_ports(
    kwargs: dict[str, object],
    expected_message: str,
) -> None:
    init_kwargs = {"host_port": unused_port(), **kwargs}

    with pytest.raises(ConfigurationError, match=expected_message):
        service_class()(**init_kwargs)


@pytest.mark.parametrize(
    "upload_endpoint_path",
    [
        "",
        "   ",
        "no-slash",
        "/",
    ],
)
def test_init_rejects_invalid_upload_endpoint_path(
    upload_endpoint_path: str,
) -> None:
    with pytest.raises(ConfigurationError, match="upload_endpoint_path"):
        service_class()(
            host_port=unused_port(),
            upload_endpoint_path=upload_endpoint_path,
        )


def test_container_port_defaults_to_host_port_when_not_provided() -> None:
    host_port = unused_port()
    plugin = service_class()(host_port=host_port)

    assert plugin.host_port == host_port
    assert plugin.container_port == host_port


def test_container_port_can_differ_from_host_port() -> None:
    host_port = unused_port()
    container_port = unused_port()
    plugin = service_class()(host_port=host_port, container_port=container_port)

    assert plugin.host_port == host_port
    assert plugin.container_port == container_port


# ---------------------------------------------------------------------------
# configure_container contract — mount metadata requirements
# ---------------------------------------------------------------------------


def test_configure_container_requires_mount_metadata_in_state() -> None:
    plugin = service_class()(host_port=unused_port())

    with pytest.raises(ConfigurationError, match="mount metadata"):
        ContainerBuilderService(plugins=[plugin])._prepare_specs()


def test_configure_container_rejects_non_mount_metadata_type_in_state() -> None:
    plugin = service_class()(host_port=unused_port())

    container = ContainerSpec(name="test", image_tag="test:latest")
    container.state[MOUNT_METADATA_STATE_KEY] = "not-a-MountMetadata"

    with pytest.raises(ConfigurationError, match="mount metadata"):
        plugin.configure_container(container)


def test_configure_container_rejects_state_without_mount_key() -> None:
    plugin = service_class()(host_port=unused_port())

    container = ContainerSpec(name="test", image_tag="test:latest")
    # key is not set at all

    with pytest.raises(ConfigurationError, match="mount metadata"):
        plugin.configure_container(container)


# ---------------------------------------------------------------------------
# configure_container contract — no startup directory creation
# ---------------------------------------------------------------------------


def test_configure_container_does_not_add_inbox_startup_task() -> None:
    plugin = service_class()(host_port=unused_port())
    metadata = mount_metadata(
        container_path=PurePosixPath("/workspace/project"),
    )

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[_MountStatePlugin(metadata), plugin]
    )._prepare_specs()

    assert not [
        task
        for task in container_spec.startup_tasks
        if "inbox" in " ".join(task.command).lower()
    ]


def test_configure_container_managed_process_uses_mount_metadata_container_path() -> None:
    plugin = service_class()(host_port=unused_port())
    metadata = mount_metadata(
        container_path=PurePosixPath("/mnt/drive/folder"),
    )

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[_MountStatePlugin(metadata), plugin]
    )._prepare_specs()

    inbox_processes = [
        process
        for process in container_spec.managed_processes
        if "inbox" in repr(process).lower()
    ]
    assert len(inbox_processes) == 1
    assert "/mnt/drive/folder" in repr(inbox_processes[0])


# ---------------------------------------------------------------------------
# configure_container contract — managed process (FastAPI upload server)
# ---------------------------------------------------------------------------


def test_configure_container_adds_inbox_upload_managed_process() -> None:
    plugin = service_class()(host_port=unused_port())
    metadata = mount_metadata()

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[_MountStatePlugin(metadata), plugin]
    )._prepare_specs()

    inbox_processes = [
        p for p in container_spec.managed_processes
        if "inbox" in repr(p).lower()
    ]
    assert len(inbox_processes) == 1


def test_configure_container_publishes_container_port_to_host_port() -> None:
    host_port = unused_port()
    container_port = unused_port()
    plugin = service_class()(host_port=host_port, container_port=container_port)
    metadata = mount_metadata()

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[_MountStatePlugin(metadata), plugin]
    )._prepare_specs()

    assert container_spec.ports == {container_port: host_port}


def test_configure_container_managed_process_contains_endpoint_path() -> None:
    plugin = service_class()(
        host_port=unused_port(),
        upload_endpoint_path="/api/custom/upload",
    )
    metadata = mount_metadata()

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[_MountStatePlugin(metadata), plugin]
    )._prepare_specs()

    inbox_processes = [
        p for p in container_spec.managed_processes
        if "inbox" in repr(p).lower()
    ]
    assert len(inbox_processes) == 1
    assert "/api/custom/upload" in repr(inbox_processes[0])


def test_configure_container_managed_process_contains_container_path() -> None:
    plugin = service_class()(host_port=unused_port())
    metadata = mount_metadata(
        container_path=PurePosixPath("/workspace/project"),
    )

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[_MountStatePlugin(metadata), plugin]
    )._prepare_specs()

    inbox_processes = [
        p for p in container_spec.managed_processes
        if "inbox" in repr(p).lower()
    ]
    assert len(inbox_processes) == 1
    assert str(metadata.container_path) in repr(inbox_processes[0])


# ---------------------------------------------------------------------------
# configure_container contract — doesn't modify unrelated specs
# ---------------------------------------------------------------------------


def test_configure_container_does_not_set_command() -> None:
    plugin = service_class()(host_port=unused_port())
    metadata = mount_metadata()

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[_MountStatePlugin(metadata), plugin]
    )._prepare_specs()

    assert container_spec.command is None


def test_configure_container_does_not_overwrite_existing_command() -> None:
    plugin = service_class()(host_port=unused_port())
    metadata = mount_metadata()

    container = ContainerSpec(name="test", image_tag="test:latest")
    container.state[MOUNT_METADATA_STATE_KEY] = metadata
    container.command = ["opencode", "serve"]

    plugin.configure_container(container)

    assert container.command == ["opencode", "serve"]


def test_configure_container_does_not_set_working_dir() -> None:
    plugin = service_class()(host_port=unused_port())
    metadata = mount_metadata()

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[_MountStatePlugin(metadata), plugin]
    )._prepare_specs()

    assert container_spec.working_dir is None


def test_configure_container_does_not_overwrite_existing_working_dir() -> None:
    plugin = service_class()(host_port=unused_port())
    metadata = mount_metadata()

    container = ContainerSpec(name="test", image_tag="test:latest")
    container.state[MOUNT_METADATA_STATE_KEY] = metadata
    container.working_dir = PurePosixPath("/workspace/workdir")

    plugin.configure_container(container)

    assert container.working_dir == PurePosixPath("/workspace/workdir")


def test_configure_container_does_not_overwrite_other_state_entries() -> None:
    plugin = service_class()(host_port=unused_port())
    metadata = mount_metadata()

    container = ContainerSpec(name="test", image_tag="test:latest")
    container.state[MOUNT_METADATA_STATE_KEY] = metadata
    container.state["other_key"] = "other_value"

    plugin.configure_container(container)

    assert "other_key" in container.state
    assert container.state["other_key"] == "other_value"
    assert container.state[MOUNT_METADATA_STATE_KEY] is metadata


def test_configure_container_does_not_add_unexpected_volumes() -> None:
    plugin = service_class()(host_port=unused_port())
    metadata = mount_metadata()

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[_MountStatePlugin(metadata), plugin]
    )._prepare_specs()

    assert container_spec.volumes == {}


def test_configure_container_does_not_set_home_or_xdg_env() -> None:
    plugin = service_class()(host_port=unused_port())
    metadata = mount_metadata()

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[_MountStatePlugin(metadata), plugin]
    )._prepare_specs()

    assert not {"HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"} & set(container_spec.env)


# ---------------------------------------------------------------------------
# configure_container contract — source_type independence
# ---------------------------------------------------------------------------


def test_configure_container_works_with_local_mount_source() -> None:
    plugin = service_class()(host_port=unused_port())
    metadata = mount_metadata(source_type="local")

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[_MountStatePlugin(metadata), plugin]
    )._prepare_specs()

    assert container_spec.state[MOUNT_METADATA_STATE_KEY].source_type == "local"
    assert container_spec.startup_tasks == []


def test_configure_container_works_with_remote_mount_source() -> None:
    plugin = service_class()(host_port=unused_port())
    metadata = mount_metadata(
        source_type="remote",
        remote_name="gdrive",
        host_path=None,
    )

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[_MountStatePlugin(metadata), plugin]
    )._prepare_specs()

    assert container_spec.state[MOUNT_METADATA_STATE_KEY].source_type == "remote"
    assert container_spec.startup_tasks == []


def test_configure_container_behavior_identical_for_local_and_remote() -> None:
    port = unused_port()
    plugin_local = service_class()(host_port=port)
    plugin_remote = service_class()(host_port=port)

    metadata_local = mount_metadata(
        container_path=PurePosixPath("/workspace/test"),
        source_type="local",
    )
    metadata_remote = mount_metadata(
        container_path=PurePosixPath("/workspace/test"),
        source_type="remote",
        remote_name="gdrive",
        host_path=None,
    )

    _img_l, ctr_l = ContainerBuilderService(
        plugins=[_MountStatePlugin(metadata_local), plugin_local]
    )._prepare_specs()
    _img_r, ctr_r = ContainerBuilderService(
        plugins=[_MountStatePlugin(metadata_remote), plugin_remote]
    )._prepare_specs()

    assert ctr_l.startup_tasks == ctr_r.startup_tasks == []
    assert ctr_l.ports == ctr_r.ports
    assert ctr_l.command is None and ctr_r.command is None
    assert ctr_l.working_dir is None and ctr_r.working_dir is None


# ---------------------------------------------------------------------------
# configure_image contract
# ---------------------------------------------------------------------------


def test_configure_image_installs_python3() -> None:
    plugin = service_class()(host_port=unused_port())

    image_spec = ImageSpec()
    plugin.configure_image(image_spec)

    run_commands = "\n".join(image_spec.run_commands)
    assert "python3" in run_commands


def test_configure_image_can_install_fastapi_dependencies() -> None:
    plugin = service_class()(host_port=unused_port())

    image_spec = ImageSpec()
    plugin.configure_image(image_spec)

    dependency_commands = "\n".join(
        command
        for command in image_spec.run_commands
        if "fastapi" in command
        or "uvicorn" in command
        or "python-multipart" in command
    )
    assert "fastapi" in dependency_commands
    assert "uvicorn" in dependency_commands
    assert "python-multipart" in dependency_commands

    for index, command in enumerate(image_spec.run_commands):
        if "python3 -m pip" not in command:
            continue

        commands_before_pip_use = "\n".join(image_spec.run_commands[: index + 1])
        assert (
            "py3-pip" in commands_before_pip_use
            or "ensurepip" in commands_before_pip_use
            or "get-pip.py" in commands_before_pip_use
        ), "configure_image uses python3 -m pip before installing pip"


def test_configure_image_copies_upload_handler_files_to_container() -> None:
    plugin = service_class()(host_port=unused_port())

    image_spec = ImageSpec()
    plugin.configure_image(image_spec)

    install_commands = "\n".join(image_spec.run_commands)
    assert "inbox_upload_handler" in install_commands


def test_configure_image_keeps_run_commands_below_line_limit() -> None:
    plugin = service_class()(host_port=unused_port())

    image_spec = ImageSpec()
    plugin.configure_image(image_spec)

    assert max(len(command) for command in image_spec.run_commands) < 65_535


def test_configure_image_does_not_set_base_image_or_command() -> None:
    plugin = service_class()(host_port=unused_port())

    original = ImageSpec()
    image = ImageSpec()
    plugin.configure_image(image)

    assert image.base_image == original.base_image
    assert image.workdir == original.workdir
    assert image.command == original.command
    assert image.env == original.env


# ---------------------------------------------------------------------------
# post_start contract
# ---------------------------------------------------------------------------


def test_post_start_does_not_start_host_side_server() -> None:
    plugin = service_class()(host_port=unused_port())
    metadata = mount_metadata()

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[_MountStatePlugin(metadata), plugin]
    )._prepare_specs()

    runtime = ContainerRuntimeContext(
        docker_client=object(),
        container=FakeContainer(),
        state=container_spec.state,
    )
    # Should not raise and should not start host-side listeners
    plugin.post_start(runtime)
    assert runtime.container.commands
    assert "/api/inbox/upload" in runtime.container.commands[0][2]


def test_post_start_does_not_modify_state() -> None:
    plugin = service_class()(host_port=unused_port())
    metadata = mount_metadata()

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[_MountStatePlugin(metadata), plugin]
    )._prepare_specs()

    state_before = set(container_spec.state.keys())
    runtime = ContainerRuntimeContext(
        docker_client=object(),
        container=FakeContainer(),
        state=container_spec.state,
    )
    plugin.post_start(runtime)

    assert set(container_spec.state.keys()) == state_before


def test_post_start_checks_remote_mount_before_upload_probe() -> None:
    plugin = service_class()(host_port=unused_port())
    metadata = mount_metadata(source_type="remote", remote_name="gdrive")

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[_MountStatePlugin(metadata), plugin]
    )._prepare_specs()

    container = FakeContainer()
    plugin.post_start(
        ContainerRuntimeContext(
            docker_client=object(),
            container=container,
            state=container_spec.state,
        )
    )

    assert "mountpoint" in container.commands[0][2]
    assert "rclone" in container.commands[0][2]


def test_post_start_fails_when_upload_probe_fails() -> None:
    plugin = service_class()(host_port=unused_port())
    metadata = mount_metadata()

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[_MountStatePlugin(metadata), plugin]
    )._prepare_specs()

    with pytest.raises(RuntimeError, match="inbox upload health check failed"):
        plugin.post_start(
            ContainerRuntimeContext(
                docker_client=object(),
                container=FakeContainer(exit_code=1, output="endpoint down"),
                state=container_spec.state,
            )
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MountStatePlugin:
    name = "mount-state"

    def __init__(self, metadata: object) -> None:
        self.metadata = metadata

    def configure_image(self, image: ImageSpec) -> None:
        return None

    def configure_container(self, container: ContainerSpec) -> None:
        container.state[MOUNT_METADATA_STATE_KEY] = self.metadata

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        return None
