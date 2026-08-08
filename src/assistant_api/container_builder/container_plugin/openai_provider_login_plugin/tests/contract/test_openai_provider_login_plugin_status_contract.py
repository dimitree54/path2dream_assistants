from __future__ import annotations

from pathlib import Path

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin.openai_provider_login_plugin._auth_server import (
    OpenAIProviderLoginError,
)
from assistant_api.container_builder.container_plugin.openai_provider_login_plugin import (
    _credential_validator,
)
from openai_provider_login_contract_helpers import (
    OpenCodeRuntimeStatePlugin,
    VALID_STATUS_STATES,
    service_class,
    start_plugin,
    status_json,
    status_response,
    write_openai_oauth_auth,
)
from openai_provider_stub import (
    OpenAIProviderEnv,
    OpenCodeProviderStub,
    openai_provider_env,
    opencode_provider_stub,
)


@pytest.fixture(autouse=True)
def _isolated_opencode_auth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))


def test_startup_fails_when_opencode_server_is_unavailable(
    openai_provider_env: OpenAIProviderEnv,
    opencode_provider_stub: OpenCodeProviderStub,
) -> None:
    opencode_provider_stub.state.unavailable = True
    plugin = service_class()(host_port=openai_provider_env.openai_auth_port)
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[OpenCodeRuntimeStatePlugin(openai_provider_env.opencode_api_port), plugin]
    )._prepare_specs()

    with pytest.raises(OpenAIProviderLoginError, match="OpenCode"):
        start_plugin(plugin, container_spec.state)


def test_startup_does_not_probe_openai_provider_before_auth_credentials_exist(
    openai_provider_env: OpenAIProviderEnv,
    opencode_provider_stub: OpenCodeProviderStub,
) -> None:
    opencode_provider_stub.state.include_openai_provider = False
    plugin = service_class()(host_port=openai_provider_env.openai_auth_port)
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[OpenCodeRuntimeStatePlugin(openai_provider_env.opencode_api_port), plugin]
    )._prepare_specs()

    start_plugin(plugin, container_spec.state)
    status = status_json(port=openai_provider_env.openai_auth_port)

    assert status["state"] == "unauthenticated"
    assert opencode_provider_stub.state.provider_requests == 0
    assert opencode_provider_stub.state.auth_requests == 1


def test_status_without_openai_auth_credentials_does_not_call_provider(
    openai_provider_env: OpenAIProviderEnv,
    opencode_provider_stub: OpenCodeProviderStub,
) -> None:
    plugin = service_class()(host_port=openai_provider_env.openai_auth_port)
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[OpenCodeRuntimeStatePlugin(openai_provider_env.opencode_api_port), plugin]
    )._prepare_specs()
    start_plugin(plugin, container_spec.state)

    status = status_json(port=openai_provider_env.openai_auth_port)

    assert status["state"] == "unauthenticated"
    assert status["authValid"] is False
    assert opencode_provider_stub.state.provider_requests == 0


def test_startup_fails_when_headless_openai_oauth_is_missing(
    openai_provider_env: OpenAIProviderEnv,
    opencode_provider_stub: OpenCodeProviderStub,
) -> None:
    opencode_provider_stub.state.include_headless_method = False
    plugin = service_class()(host_port=openai_provider_env.openai_auth_port)
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[OpenCodeRuntimeStatePlugin(openai_provider_env.opencode_api_port), plugin]
    )._prepare_specs()

    with pytest.raises(OpenAIProviderLoginError, match="headless"):
        start_plugin(plugin, container_spec.state)


def test_status_reports_required_json_shape_when_openai_is_not_connected(
    openai_provider_env: OpenAIProviderEnv,
) -> None:
    plugin = service_class()(host_port=openai_provider_env.openai_auth_port)
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[OpenCodeRuntimeStatePlugin(openai_provider_env.opencode_api_port), plugin]
    )._prepare_specs()
    start_plugin(plugin, container_spec.state)

    status = status_json(port=openai_provider_env.openai_auth_port)

    assert {"authValid", "state", "message", "providerName"} <= set(status)
    assert status["providerName"] == "OpenAI"
    assert status["authValid"] is False
    assert status["state"] == "unauthenticated"
    assert status["state"] in VALID_STATUS_STATES
    assert isinstance(status["message"], str)


def test_status_does_not_report_authenticated_when_provider_is_only_connected(
    openai_provider_env: OpenAIProviderEnv,
    opencode_provider_stub: OpenCodeProviderStub,
) -> None:
    opencode_provider_stub.state.connected = True
    plugin = service_class()(host_port=openai_provider_env.openai_auth_port)
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[OpenCodeRuntimeStatePlugin(openai_provider_env.opencode_api_port), plugin]
    )._prepare_specs()
    start_plugin(plugin, container_spec.state)

    status = status_json(port=openai_provider_env.openai_auth_port)

    assert status["providerName"] == "OpenAI"
    assert status["authValid"] is False
    assert status["state"] == "unauthenticated"
    assert status["state"] in VALID_STATUS_STATES


def test_status_reports_authenticated_when_openai_oauth_credentials_exist(
    openai_provider_env: OpenAIProviderEnv,
    opencode_provider_stub: OpenCodeProviderStub,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_home = tmp_path / "share"
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    write_openai_oauth_auth(data_home)
    plugin = service_class()(host_port=openai_provider_env.openai_auth_port)
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[OpenCodeRuntimeStatePlugin(openai_provider_env.opencode_api_port), plugin]
    )._prepare_specs()
    start_plugin(plugin, container_spec.state)

    status = status_json(port=openai_provider_env.openai_auth_port)

    assert status["providerName"] == "OpenAI"
    assert status["authValid"] is True
    assert status["state"] == "authenticated"
    assert "oauth" in status["message"]
    assert opencode_provider_stub.state.provider_requests >= 1
    assert all(opencode_provider_stub.state.provider_request_auth_present)


def test_status_revalidates_cached_credentials_after_their_expiry(
    openai_provider_env: OpenAIProviderEnv,
    opencode_provider_stub: OpenCodeProviderStub,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_home = tmp_path / "share"
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    write_openai_oauth_auth(data_home)
    plugin = service_class()(host_port=openai_provider_env.openai_auth_port)
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[OpenCodeRuntimeStatePlugin(openai_provider_env.opencode_api_port), plugin]
    )._prepare_specs()
    start_plugin(plugin, container_spec.state)
    assert status_json(port=openai_provider_env.openai_auth_port)["authValid"] is True
    assert len(opencode_provider_stub.state.message_requests) == 1

    opencode_provider_stub.state.provider_auth_rejected = True
    monkeypatch.setattr(_credential_validator, "_current_time_ms", lambda: 10**18)
    status = status_json(port=openai_provider_env.openai_auth_port)

    assert status["authValid"] is False
    assert status["state"] == "unauthenticated"
    assert len(opencode_provider_stub.state.message_requests) == 2


def test_status_fails_fast_when_authenticated_provider_payload_has_no_openai(
    openai_provider_env: OpenAIProviderEnv,
    opencode_provider_stub: OpenCodeProviderStub,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_home = tmp_path / "share"
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    write_openai_oauth_auth(data_home)
    opencode_provider_stub.state.include_openai_provider = False
    plugin = service_class()(host_port=openai_provider_env.openai_auth_port)
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[OpenCodeRuntimeStatePlugin(openai_provider_env.opencode_api_port), plugin]
    )._prepare_specs()

    with pytest.raises(RuntimeError, match="OpenAI provider login health check failed"):
        start_plugin(plugin, container_spec.state)

    assert all(opencode_provider_stub.state.provider_request_auth_present)


def test_later_opencode_outage_reports_unavailable_json(
    openai_provider_env: OpenAIProviderEnv,
    opencode_provider_stub: OpenCodeProviderStub,
) -> None:
    plugin = service_class()(host_port=openai_provider_env.openai_auth_port)
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[OpenCodeRuntimeStatePlugin(openai_provider_env.opencode_api_port), plugin]
    )._prepare_specs()
    start_plugin(plugin, container_spec.state)

    opencode_provider_stub.state.unavailable = True
    response = status_response(port=openai_provider_env.openai_auth_port)
    payload = response.json()

    assert response.status >= 500
    assert payload["authValid"] is False
    assert payload["state"] == "unavailable"
    assert payload["state"] in VALID_STATUS_STATES
    assert payload["message"]
