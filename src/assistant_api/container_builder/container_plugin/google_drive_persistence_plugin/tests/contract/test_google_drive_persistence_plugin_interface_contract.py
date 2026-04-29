from __future__ import annotations

import inspect
from pathlib import PurePosixPath

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.models import ContainerRuntimeContext
from google_drive_persistence_contract_helpers import persistence_service_class


class RecordingContainer:
    def __init__(self, exit_code: int = 0, output: str = "") -> None:
        self.exit_code = exit_code
        self.output = output
        self.commands: list[list[str]] = []

    def exec_run(self, command: list[str]) -> object:
        self.commands.append(command)
        exit_code = self.exit_code
        output = self.output.encode("utf-8")

        class Result:
            pass

        result = Result()
        result.exit_code = exit_code
        result.output = output
        return result


def test_public_service_import_and_init_signature_defaults() -> None:
    signature = inspect.signature(persistence_service_class())

    assert list(signature.parameters) == [
        "config_volume",
        "cache_volume",
        "config_dir",
        "cache_dir",
    ]
    assert signature.parameters["config_volume"].default == "notes_assistant_api_google_drive_config"
    assert signature.parameters["cache_volume"].default == "notes_assistant_api_google_drive_cache"
    assert signature.parameters["config_dir"].default == PurePosixPath(
        "/tmp/google-drive-persistence/rclone-config"
    )
    assert signature.parameters["cache_dir"].default == PurePosixPath(
        "/tmp/google-drive-persistence/rclone-cache"
    )


def test_default_plugin_adds_only_rclone_env_and_named_volumes() -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[persistence_service_class()()]
    )._prepare_specs()

    assert container_spec.env == {
        "RCLONE_CONFIG": "/tmp/google-drive-persistence/rclone-config/rclone.conf",
        "RCLONE_CACHE_DIR": "/tmp/google-drive-persistence/rclone-cache",
    }
    assert set(container_spec.volumes) == {
        "notes_assistant_api_google_drive_config",
        "notes_assistant_api_google_drive_cache",
    }
    assert container_spec.volumes["notes_assistant_api_google_drive_config"].source == (
        "notes_assistant_api_google_drive_config"
    )
    assert container_spec.volumes["notes_assistant_api_google_drive_config"].target == PurePosixPath(
        "/tmp/google-drive-persistence/rclone-config"
    )
    assert container_spec.volumes["notes_assistant_api_google_drive_config"].type == "volume"
    assert container_spec.volumes["notes_assistant_api_google_drive_cache"].source == (
        "notes_assistant_api_google_drive_cache"
    )
    assert container_spec.volumes["notes_assistant_api_google_drive_cache"].target == PurePosixPath(
        "/tmp/google-drive-persistence/rclone-cache"
    )
    assert container_spec.volumes["notes_assistant_api_google_drive_cache"].type == "volume"


def test_plugin_does_not_claim_unrelated_persistence_or_runtime_responsibilities() -> None:
    image_spec, container_spec = ContainerBuilderService(
        plugins=[persistence_service_class()()]
    )._prepare_specs()

    assert image_spec.run_commands == ["mkdir -p /workspace"]
    assert not {"HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"} & set(
        container_spec.env
    )
    assert container_spec.ports == {}
    assert container_spec.command is None
    assert container_spec.working_dir is None
    assert container_spec.devices == []
    assert container_spec.cap_add == []
    assert container_spec.security_opt == []
    assert container_spec.startup_tasks == []
    assert container_spec.managed_processes == []
    assert container_spec.state == {}

    recording_container = RecordingContainer()
    runtime = ContainerRuntimeContext(
        docker_client=object(),
        container=recording_container,
        state=container_spec.state,
    )
    persistence_service_class()().post_start(runtime)
    assert recording_container.commands
    assert "rclone-config" in recording_container.commands[0][2]
    assert "rclone-cache" in recording_container.commands[0][2]


def test_post_start_fails_when_persisted_state_dirs_are_unhealthy() -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[persistence_service_class()()]
    )._prepare_specs()

    with pytest.raises(RuntimeError, match="Google Drive persistence health check failed"):
        persistence_service_class()().post_start(
            ContainerRuntimeContext(
                docker_client=object(),
                container=RecordingContainer(exit_code=1, output="read only"),
                state=container_spec.state,
            )
        )


def test_custom_volume_names_and_paths_are_used_exactly() -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[
            persistence_service_class()(
                config_volume="custom-rclone-config-volume",
                cache_volume="custom-rclone-cache-volume",
                config_dir=PurePosixPath("/state/rclone/config"),
                cache_dir=PurePosixPath("/state/rclone/cache"),
            )
        ]
    )._prepare_specs()

    assert container_spec.env == {
        "RCLONE_CONFIG": "/state/rclone/config/rclone.conf",
        "RCLONE_CACHE_DIR": "/state/rclone/cache",
    }
    assert set(container_spec.volumes) == {
        "custom-rclone-config-volume",
        "custom-rclone-cache-volume",
    }
    assert container_spec.volumes["custom-rclone-config-volume"].target == PurePosixPath(
        "/state/rclone/config"
    )
    assert container_spec.volumes["custom-rclone-cache-volume"].target == PurePosixPath(
        "/state/rclone/cache"
    )
