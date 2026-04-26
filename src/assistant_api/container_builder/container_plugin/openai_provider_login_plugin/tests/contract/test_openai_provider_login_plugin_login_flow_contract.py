from __future__ import annotations

from assistant_api.container_builder import ContainerBuilderService
from openai_provider_login_contract_helpers import (
    read_url,
    service_class,
    service_url,
    start_plugin,
    status_json,
    wait_for_status_state,
)
from openai_provider_stub import (
    OpenAIProviderEnv,
    OpenCodeProviderStub,
    openai_provider_env,
    opencode_provider_stub,
)


def test_login_selects_headless_openai_oauth_method_and_renders_user_code(
    openai_provider_env: OpenAIProviderEnv,
    opencode_provider_stub: OpenCodeProviderStub,
) -> None:
    opencode_provider_stub.state.callback_connects_auth = False
    plugin = service_class()()
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()
    start_plugin(plugin, container_spec.state)

    login = read_url(service_url("/login"))

    assert login.status == 200
    assert "text/html" in login.headers.get("Content-Type", "")
    assert opencode_provider_stub.state.authorize_requests == [{"method": 1}]
    assert opencode_provider_stub.state.authorization_url in login.text
    assert opencode_provider_stub.state.instructions in login.text
    assert "OPENAI-CONTRACT-CODE" in login.text
    assert "localhost:1455" not in login.text
    assert "/auth/callback" not in login.text


def test_successful_headless_callback_completion_reports_authenticated(
    openai_provider_env: OpenAIProviderEnv,
    opencode_provider_stub: OpenCodeProviderStub,
) -> None:
    plugin = service_class()()
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()
    start_plugin(plugin, container_spec.state)

    login = read_url(service_url("/login"))
    complete = read_url(service_url("/login?complete=1"))
    status = wait_for_status_state("authenticated")

    assert login.status == 200
    assert complete.status == 200
    assert status["authValid"] is True
    assert opencode_provider_stub.state.callback_requests == [{"method": 1}]


def test_repeated_login_while_auth_is_pending_does_not_duplicate_authorize_calls(
    openai_provider_env: OpenAIProviderEnv,
    opencode_provider_stub: OpenCodeProviderStub,
) -> None:
    opencode_provider_stub.state.callback_connects_auth = False
    plugin = service_class()()
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()
    start_plugin(plugin, container_spec.state)

    first_login = read_url(service_url("/login"))
    second_login = read_url(service_url("/login"))

    assert first_login.status == 200
    assert second_login.status == 200
    assert opencode_provider_stub.state.authorize_requests == [{"method": 1}]


def test_login_when_already_authenticated_does_not_restart_oauth(
    openai_provider_env: OpenAIProviderEnv,
    opencode_provider_stub: OpenCodeProviderStub,
) -> None:
    opencode_provider_stub.state.connected = True
    plugin = service_class()()
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()
    start_plugin(plugin, container_spec.state)

    login = read_url(service_url("/login"))
    status = status_json()

    assert login.status == 200
    assert status["state"] == "authenticated"
    assert opencode_provider_stub.state.authorize_requests == []
    assert opencode_provider_stub.state.callback_requests == []


def test_authorize_failure_sets_error_without_fallback_to_other_auth_methods(
    openai_provider_env: OpenAIProviderEnv,
    opencode_provider_stub: OpenCodeProviderStub,
) -> None:
    opencode_provider_stub.state.authorize_failure = True
    plugin = service_class()()
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()
    start_plugin(plugin, container_spec.state)

    login = read_url(service_url("/login"))
    status = wait_for_status_state("error")

    assert login.status >= 500
    assert status["authValid"] is False
    assert opencode_provider_stub.state.authorize_requests == [{"method": 1}]
    assert opencode_provider_stub.state.callback_requests == []


def test_callback_failure_sets_error_without_redirect_or_api_key_fallback(
    openai_provider_env: OpenAIProviderEnv,
    opencode_provider_stub: OpenCodeProviderStub,
) -> None:
    opencode_provider_stub.state.callback_failure = True
    plugin = service_class()()
    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()
    start_plugin(plugin, container_spec.state)

    login = read_url(service_url("/login"))
    complete = read_url(service_url("/login?complete=1"))
    status = wait_for_status_state("error")

    assert login.status == 200
    assert complete.status >= 500
    assert status["authValid"] is False
    assert opencode_provider_stub.state.authorize_requests == [{"method": 1}]
    assert opencode_provider_stub.state.callback_requests == [{"method": 1}]
