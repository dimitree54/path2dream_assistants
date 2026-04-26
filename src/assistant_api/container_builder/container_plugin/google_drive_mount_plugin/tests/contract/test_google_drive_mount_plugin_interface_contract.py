from __future__ import annotations

import inspect
import json
from pathlib import PurePosixPath

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.container_builder.container_plugin import MOUNT_METADATA_STATE_KEY
from assistant_api.models import MountMetadata
from google_drive_mount_contract_helpers import REQUIRED_ENV, auth_port, service_class, unused_port
from google_drive_mount_oauth_stub import google_env


def test_public_service_import_and_init_signature_defaults() -> None:
    signature = inspect.signature(service_class())

    assert list(signature.parameters) == [
        "container_path",
        "remote_name",
        "mode",
        "oauth_authorize_url",
        "oauth_token_url",
        "drive_api_base_url",
    ]
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
    monkeypatch.setenv("GOOGLE_DRIVE_AUTH_PORT", str(unused_port()))
    monkeypatch.delenv(missing_env)

    with pytest.raises(ConfigurationError, match=missing_env):
        service_class()()


@pytest.mark.parametrize("invalid_port", ["", "not-a-port", "0", "65536"])
def test_init_requires_valid_google_drive_auth_port(
    google_env: str,
    monkeypatch: pytest.MonkeyPatch,
    invalid_port: str,
) -> None:
    monkeypatch.setenv("GOOGLE_DRIVE_AUTH_PORT", invalid_port)

    with pytest.raises(ConfigurationError, match="GOOGLE_DRIVE_AUTH_PORT"):
        service_class()()


@pytest.mark.parametrize(
    "credentials_json",
    [
        "",
        "not-json",
        json.dumps({"installed": {"client_id": "client-id", "client_secret": "client-secret"}}),
        json.dumps({"web": {"client_secret": "client-secret"}}),
        json.dumps({"web": {"client_id": "client-id"}}),
    ],
)
def test_init_requires_valid_google_oauth_web_credentials_json(
    google_env: str,
    monkeypatch: pytest.MonkeyPatch,
    credentials_json: str,
) -> None:
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON", credentials_json)

    with pytest.raises(ConfigurationError, match="GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON"):
        service_class()()


def test_prepare_specs_publishes_auth_port_fuse_capabilities_and_remote_metadata(
    google_env: str,
) -> None:
    host_port = auth_port()
    plugin = service_class()()

    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()

    assert container_spec.ports == {host_port: host_port}
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
