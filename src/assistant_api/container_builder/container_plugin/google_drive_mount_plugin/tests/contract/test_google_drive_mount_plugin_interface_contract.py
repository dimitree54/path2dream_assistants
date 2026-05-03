from __future__ import annotations

import inspect
import json
from pathlib import PurePosixPath

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.container_builder.container_plugin import (
    MOUNT_METADATA_STATE_KEY,
)
from assistant_api.container_builder.container_plugin.opencode_web_server_plugin import (
    OpenCodeWebServerPluginService,
)
from assistant_api.models import ContainerRuntimeContext, MountMetadata, PublishedPort
from google_drive_mount_contract_helpers import REQUIRED_ENV, auth_port, service_class, unused_port
from google_drive_mount_oauth_stub import google_env


def test_public_service_import_and_init_signature_defaults() -> None:
    signature = inspect.signature(service_class())

    assert list(signature.parameters) == [
        "host_port",
        "drive_folder_name",
        "workspace_subdir_name",
        "container_path",
        "auth_container_port",
        "remote_name",
        "mode",
        "oauth_authorize_url",
        "oauth_token_url",
        "drive_api_base_url",
        "public_base_url",
        "enable_local_folder_import",
        "host",
    ]
    assert signature.parameters["host_port"].default is inspect.Parameter.empty
    assert signature.parameters["drive_folder_name"].default is inspect.Parameter.empty
    assert signature.parameters["workspace_subdir_name"].default is None
    assert signature.parameters["container_path"].default is None
    assert signature.parameters["auth_container_port"].default is None
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
    assert signature.parameters["public_base_url"].default is None
    assert signature.parameters["enable_local_folder_import"].default is False
    assert signature.parameters["host"].default is None


@pytest.mark.parametrize("missing_env", REQUIRED_ENV)
def test_init_requires_google_oauth_env(
    monkeypatch: pytest.MonkeyPatch,
    missing_env: str,
) -> None:
    for env_name in REQUIRED_ENV:
        monkeypatch.setenv(env_name, f"value-for-{env_name}")
    monkeypatch.delenv(missing_env)

    with pytest.raises(ConfigurationError, match=missing_env):
        service_class()(host_port=unused_port(), drive_folder_name="Drive Folder")


def test_init_does_not_require_port_or_folder_env(
    google_env: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GOOGLE_DRIVE_AUTH_PORT", raising=False)
    monkeypatch.delenv("GOOGLE_DRIVE_MOUNT_FOLDER_NAME", raising=False)

    plugin = service_class()(host_port=unused_port(), drive_folder_name=google_env)

    assert plugin.folder_name == google_env


@pytest.mark.parametrize(
    ("kwargs", "expected_message"),
    [
        ({"host_port": 0}, "host_port"),
        ({"host_port": 65536}, "host_port"),
        ({"host_port": "not-a-port"}, "host_port"),
        ({"auth_container_port": 0}, "auth_container_port"),
        ({"auth_container_port": 65536}, "auth_container_port"),
        ({"auth_container_port": "not-a-port"}, "auth_container_port"),
        ({"host": "localhost"}, "host"),
    ],
)
def test_init_requires_valid_google_drive_auth_ports(
    google_env: str,
    kwargs: dict[str, object],
    expected_message: str,
) -> None:
    init_kwargs = {"host_port": unused_port(), "drive_folder_name": google_env, **kwargs}

    with pytest.raises(ConfigurationError, match=expected_message):
        service_class()(**init_kwargs)


def test_init_requires_drive_folder_name(google_env: str) -> None:
    with pytest.raises(ConfigurationError, match="drive_folder_name"):
        service_class()(host_port=unused_port(), drive_folder_name="")


@pytest.mark.parametrize(
    "invalid_url",
    [
        "",
        "not-a-url",
        "/relative/path",
        "ftp://example.com",
        "http://user@example.com",
        "http://user:pass@example.com",
        "https://example.com/base?query=1",
        "https://example.com/base#fragment",
    ],
)
def test_init_rejects_invalid_public_base_url(
    google_env: str,
    invalid_url: str,
) -> None:
    with pytest.raises(ConfigurationError, match="public_base_url"):
        service_class()(
            host_port=unused_port(),
            drive_folder_name=google_env,
            public_base_url=invalid_url,
        )


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
        service_class()(host_port=unused_port(), drive_folder_name=google_env)


def test_prepare_specs_publishes_auth_port_fuse_capabilities_and_remote_metadata(
    google_env: str,
) -> None:
    host_port = auth_port()
    plugin = service_class()(
        host_port=host_port,
        drive_folder_name=google_env,
        container_path=PurePosixPath("/workspace/project"),
    )

    image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()
    install_commands = "\n".join(image_spec.run_commands)

    assert "rclone" in image_spec.apk_packages
    assert "fuse3" in image_spec.apk_packages
    assert "python3" in image_spec.apk_packages
    assert any("/workspace/project" in command for command in image_spec.run_commands)
    assert "assets/petprojectcofounder_login_page.css" in install_commands
    assert "_local_folder_import_control.py" in install_commands
    assert container_spec.ports == {host_port: host_port}
    assert container_spec.volumes == {}
    assert container_spec.command is None
    assert container_spec.working_dir is None
    assert any("GOOGLE_DRIVE_AUTH_PORT" in repr(process) for process in container_spec.managed_processes)
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


def test_prepare_specs_supports_host_bind_address(
    google_env: str,
) -> None:
    host_port = auth_port()
    plugin = service_class()(
        host_port=host_port,
        drive_folder_name=google_env,
        container_path=PurePosixPath("/workspace/project"),
        host="127.0.0.1",
    )

    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()

    assert container_spec.ports == {
        host_port: PublishedPort(host_port=host_port, host="127.0.0.1")
    }


def test_prepare_specs_mounts_directly_to_workspace_by_default(
    google_env: str,
) -> None:
    host_port = auth_port()
    plugin = service_class()(host_port=host_port, drive_folder_name=google_env)

    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()

    mount = container_spec.state[MOUNT_METADATA_STATE_KEY]
    assert isinstance(mount, MountMetadata)
    assert mount.container_path == PurePosixPath("/workspace")


def test_prepare_specs_supports_workspace_subdir_name(
    google_env: str,
) -> None:
    plugin = service_class()(
        host_port=auth_port(),
        drive_folder_name=google_env,
        workspace_subdir_name="notes",
    )

    image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()

    mount = container_spec.state[MOUNT_METADATA_STATE_KEY]
    assert isinstance(mount, MountMetadata)
    assert mount.container_path == PurePosixPath("/workspace/notes")
    assert any("/workspace/notes" in command for command in image_spec.run_commands)


@pytest.mark.parametrize("workspace_subdir_name", ["", " ", ".", "..", "/notes", "nested/notes", "nested\\notes"])
def test_init_rejects_invalid_workspace_subdir_name(
    google_env: str,
    workspace_subdir_name: str,
) -> None:
    with pytest.raises(ConfigurationError, match="workspace_subdir_name"):
        service_class()(
            host_port=auth_port(),
            drive_folder_name=google_env,
            workspace_subdir_name=workspace_subdir_name,
        )


def test_init_rejects_workspace_subdir_name_with_container_path(google_env: str) -> None:
    with pytest.raises(ConfigurationError, match="mutually exclusive"):
        service_class()(
            host_port=auth_port(),
            drive_folder_name=google_env,
            workspace_subdir_name="notes",
            container_path=PurePosixPath("/workspace/project"),
        )


def test_prepare_specs_fails_when_direct_workspace_mount_is_configured_after_opencode(
    google_env: str,
) -> None:
    plugin = service_class()(host_port=auth_port(), drive_folder_name=google_env)

    with pytest.raises(ConfigurationError, match="configured before OpenCode"):
        ContainerBuilderService(
            plugins=[OpenCodeWebServerPluginService(host_port=4097), plugin]
        )._prepare_specs()


def test_google_drive_auth_runs_as_composable_managed_process(google_env: str) -> None:
    plugin = service_class()(
        host_port=auth_port(),
        drive_folder_name=google_env,
        container_path=PurePosixPath("/workspace/project"),
    )

    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()

    managed_processes = getattr(container_spec, "managed_processes", None)
    assert managed_processes is not None, "Google Drive auth must be a container process"
    assert any("GOOGLE_DRIVE_AUTH_PORT" in repr(process) for process in managed_processes)


def test_prepare_specs_registers_managed_auth_process_without_startup_task(
    google_env: str,
) -> None:
    plugin = service_class()(
        host_port=auth_port(),
        drive_folder_name=google_env,
        container_path=PurePosixPath("/workspace/project"),
    )

    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()

    assert container_spec.startup_tasks == []
    assert any("GOOGLE_DRIVE_AUTH_PORT" in repr(process) for process in container_spec.managed_processes)


def test_configure_image_keeps_dockerfile_run_commands_below_line_limit(google_env: str) -> None:
    plugin = service_class()(
        host_port=auth_port(),
        drive_folder_name=google_env,
        container_path=PurePosixPath("/workspace/project"),
    )

    image_spec, _container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()

    assert max(len(command) for command in image_spec.run_commands) < 65_535


def test_post_start_does_not_start_host_side_auth_server(
    google_env: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import assistant_api.container_builder.container_plugin.google_drive_mount_plugin.google_drive_mount_plugin_service as service_module

    plugin = service_class()(
        host_port=auth_port(),
        drive_folder_name=google_env,
        container_path=PurePosixPath("/workspace/project"),
    )
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()

    def fail_host_start(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Google Drive auth must not start host-side server")

    monkeypatch.setattr(service_module, "ThreadingHTTPServer", fail_host_start, raising=False)

    container = _SuccessfulExecContainer()
    plugin.post_start(
        ContainerRuntimeContext(
            docker_client=object(),
            container=container,
            state=container_spec.state,
        )
    )

    assert container.commands
    assert "/status" in container.commands[0][2]
    assert "elif isinstance(payload.get('state'), str)" in container.commands[0][2]


def test_post_start_fails_when_google_drive_status_is_unhealthy(
    google_env: str,
) -> None:
    plugin = service_class()(
        host_port=auth_port(),
        drive_folder_name=google_env,
        container_path=PurePosixPath("/workspace/project"),
    )
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()

    with pytest.raises(RuntimeError, match="Google Drive mount health check failed"):
        plugin.post_start(
            ContainerRuntimeContext(
                docker_client=object(),
                container=_SuccessfulExecContainer(exit_code=1, output="status error"),
                state=container_spec.state,
            )
        )


def test_public_base_url_affects_only_auth_container_env(
    google_env: str,
) -> None:
    host_port = auth_port()
    public_base_url = "https://notes.example.com"
    plugin = service_class()(
        host_port=host_port,
        drive_folder_name=google_env,
        container_path=PurePosixPath("/workspace/project"),
        public_base_url=public_base_url,
    )

    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()

    assert container_spec.ports == {host_port: host_port}
    assert container_spec.env["GOOGLE_DRIVE_PUBLIC_BASE_URL"] == public_base_url
    assert container_spec.env["GOOGLE_DRIVE_AUTH_HOST_PORT"] == str(host_port)


def test_default_redirect_uri_remains_local_when_public_base_url_is_not_set(
    google_env: str,
) -> None:
    host_port = auth_port()
    plugin = service_class()(
        host_port=host_port,
        drive_folder_name=google_env,
        container_path=PurePosixPath("/workspace/project"),
    )

    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()

    assert container_spec.env.get("GOOGLE_DRIVE_PUBLIC_BASE_URL", "") in ("", "0")
    assert container_spec.env["GOOGLE_DRIVE_AUTH_HOST_PORT"] == str(host_port)


class _SuccessfulExecContainer:
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
