from __future__ import annotations

import inspect
from pathlib import PurePosixPath

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.container_builder.container_plugin import MOUNT_METADATA_STATE_KEY
from assistant_api.models import MountMetadata
from google_drive_mount_contract_helpers import REQUIRED_ENV, service_class, unused_port
from google_drive_mount_oauth_stub import google_env


def test_public_service_import_and_init_signature_defaults() -> None:
    signature = inspect.signature(service_class())

    assert list(signature.parameters) == [
        "host_port",
        "container_port",
        "container_path",
        "remote_name",
        "mode",
        "oauth_authorize_url",
        "oauth_token_url",
        "drive_api_base_url",
    ]
    assert signature.parameters["host_port"].default == 4102
    assert signature.parameters["container_port"].default == 4102
    assert signature.parameters["container_path"].default == PurePosixPath("/workspace/project")
    assert signature.parameters["remote_name"].default == "gdrive"
    assert signature.parameters["mode"].default == "rw"
    assert (
        signature.parameters["oauth_authorize_url"].default
        == "https://accounts.google.com/o/oauth2/v2/auth"
    )
    assert (
        signature.parameters["oauth_token_url"].default
        == "https://oauth2.googleapis.com/token"
    )
    assert (
        signature.parameters["drive_api_base_url"].default
        == "https://www.googleapis.com/drive/v3"
    )


@pytest.mark.parametrize("missing_env", REQUIRED_ENV)
def test_init_requires_google_oauth_and_folder_env(
    monkeypatch: pytest.MonkeyPatch,
    missing_env: str,
) -> None:
    for env_name in REQUIRED_ENV:
        monkeypatch.setenv(env_name, f"value-for-{env_name}")
    monkeypatch.delenv(missing_env)

    with pytest.raises(ConfigurationError, match=missing_env):
        service_class()()


def test_prepare_specs_publishes_auth_port_fuse_capabilities_and_remote_metadata(
    google_env: str,
) -> None:
    host_port = unused_port()
    plugin = service_class()(host_port=host_port, container_port=4112)

    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()

    assert container_spec.ports == {4112: host_port}
    assert container_spec.volumes == {}
    assert container_spec.command is None
    assert container_spec.working_dir is None
    assert not {"HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"} & set(container_spec.env)
    assert "/dev/fuse" in container_spec.devices
    assert "SYS_ADMIN" in container_spec.cap_add
    assert "apparmor:unconfined" in container_spec.security_opt
    mount = container_spec.state[MOUNT_METADATA_STATE_KEY]
    assert isinstance(mount, MountMetadata)
    assert mount.source_type == "remote"
    assert mount.host_path is None
    assert mount.host_basename == google_env
    assert mount.source_key == "gdrive"
    assert mount.remote_name == "gdrive"
    assert mount.remote_folder_id is None
    assert mount.container_path == PurePosixPath("/workspace/project")
    assert mount.mode == "rw"
