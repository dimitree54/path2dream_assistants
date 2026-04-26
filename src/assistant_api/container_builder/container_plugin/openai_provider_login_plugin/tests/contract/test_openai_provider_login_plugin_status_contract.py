from __future__ import annotations

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder._errors import ConfigurationError
from openai_provider_login_contract_helpers import (
    VALID_STATUS_STATES,
    service_class,
    start_plugin,
    status_json,
    status_response,
)
from openai_provider_stub import (
    OpenAIProviderEnv,
    OpenCodeProviderStub,
    openai_provider_env,
    opencode_provider_stub,
)


def test_startup_fails_when_opencode_server_is_unavailable(
    openai_provider_env: OpenAIProviderEnv,
    opencode_provider_stub: OpenCodeProviderStub,
) -> None:
    opencode_provider_stub.state.unavailable = True
    plugin = service_class()()
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()

    with pytest.raises(ConfigurationError, match="OpenCode"):
        start_plugin(plugin, container_spec.state)


def test_startup_fails_when_openai_provider_is_missing(
    openai_provider_env: OpenAIProviderEnv,
    opencode_provider_stub: OpenCodeProviderStub,
) -> None:
    opencode_provider_stub.state.include_openai_provider = False
    plugin = service_class()()
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()

    with pytest.raises(ConfigurationError, match="openai"):
        start_plugin(plugin, container_spec.state)


def test_startup_fails_when_headless_openai_oauth_is_missing(
    openai_provider_env: OpenAIProviderEnv,
    opencode_provider_stub: OpenCodeProviderStub,
) -> None:
    opencode_provider_stub.state.include_headless_method = False
    plugin = service_class()()
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()

    with pytest.raises(ConfigurationError, match="headless"):
        start_plugin(plugin, container_spec.state)


def test_status_reports_required_json_shape_when_openai_is_not_connected(
    openai_provider_env: OpenAIProviderEnv,
) -> None:
    plugin = service_class()()
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()
    start_plugin(plugin, container_spec.state)

    status = status_json()

    assert {"authValid", "state", "message", "providerName"} <= set(status)
    assert status["providerName"] == "OpenAI"
    assert status["authValid"] is False
    assert status["state"] == "unauthenticated"
    assert status["state"] in VALID_STATUS_STATES
    assert isinstance(status["message"], str)


def test_status_reports_authenticated_when_openai_provider_is_connected(
    openai_provider_env: OpenAIProviderEnv,
    opencode_provider_stub: OpenCodeProviderStub,
) -> None:
    opencode_provider_stub.state.connected = True
    plugin = service_class()()
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()
    start_plugin(plugin, container_spec.state)

    status = status_json()

    assert status["providerName"] == "OpenAI"
    assert status["authValid"] is True
    assert status["state"] == "authenticated"
    assert status["state"] in VALID_STATUS_STATES


def test_later_opencode_outage_reports_unavailable_json(
    openai_provider_env: OpenAIProviderEnv,
    opencode_provider_stub: OpenCodeProviderStub,
) -> None:
    plugin = service_class()()
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()
    start_plugin(plugin, container_spec.state)

    opencode_provider_stub.state.unavailable = True
    response = status_response()
    payload = response.json()

    assert response.status >= 500
    assert payload["authValid"] is False
    assert payload["state"] == "unavailable"
    assert payload["state"] in VALID_STATUS_STATES
    assert payload["message"]
