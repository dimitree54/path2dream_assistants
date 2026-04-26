from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import pytest

from assistant_api.container_builder import ContainerBuilderService
from openai_provider_login_contract_helpers import (
    OpenCodeRuntimeStatePlugin,
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


SHARED_STYLE_ASSET_NAME = "petprojectcofounder_login_page.css"
PENDING_STATUS_MESSAGE = (
    "Use the button above to open OpenAI authorization, enter the device code, "
    "and finish the flow. This page will update automatically."
)


def test_login_selects_headless_openai_oauth_method_and_renders_user_code(
    openai_provider_env: OpenAIProviderEnv,
    opencode_provider_stub: OpenCodeProviderStub,
) -> None:
    opencode_provider_stub.state.callback_connects_auth = False
    plugin = service_class()(host_port=openai_provider_env.openai_auth_port)
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[OpenCodeRuntimeStatePlugin(openai_provider_env.opencode_api_port), plugin]
    )._prepare_specs()
    start_plugin(plugin, container_spec.state)

    login = read_url(service_url("/login", port=openai_provider_env.openai_auth_port))

    assert login.status == 200
    assert "text/html" in login.headers.get("Content-Type", "")
    assert "<title>Connect OpenAI | Pet Project Cofounder</title>" in login.text
    assert "Pet Project Cofounder" in login.text
    assert "Connect OpenAI" in login.text
    assert "Open OpenAI authorization" in login.text
    assert "OpenAI device code" in login.text
    assert "Copy this code." in login.text
    assert '<section class="auth-card" data-device-code-card>' in login.text
    assert '<div class="actions" data-openai-auth-button>' in login.text
    assert 'data-copy-code="OPENAI-CONTRACT-CODE"' in login.text
    assert 'aria-label="Copy device code"' in login.text
    assert "copy-icon-button" in login.text
    assert "Скопировать в буфер обмена" not in login.text
    assert 'navigator.clipboard.writeText(code)' in login.text
    assert "Enter code:" not in login.text
    assert PENDING_STATUS_MESSAGE in login.text
    assert "Waiting for OpenAI provider authorization." not in login.text
    assert "No extra confirmation click is needed" not in login.text
    assert "data:image/png;base64," in login.text
    assert _style_block(login.text) == _shared_page_style()
    assert "data-auth-status" in login.text
    assert 'fetch("/login?complete=1"' in login.text
    assert 'href="/login?complete=1"' not in login.text
    assert "complete login</a>" not in login.text
    assert "<body><p>" not in login.text
    assert opencode_provider_stub.state.authorize_requests == [{"method": 1}]
    assert opencode_provider_stub.state.authorization_url in login.text
    assert "OPENAI-CONTRACT-CODE" in login.text
    assert "localhost:1455" not in login.text
    assert "/auth/callback" not in login.text


def test_login_page_can_render_from_standalone_container_module(monkeypatch) -> None:
    from assistant_api.container_builder.container_plugin.openai_provider_login_plugin import (
        _login_page,
    )

    monkeypatch.setattr(_login_page, "__package__", "")

    page = _login_page.render_login_page(
        provider_name="OpenAI",
        status={
            "authValid": False,
            "state": "unauthenticated",
            "message": "OpenAI provider is not authenticated.",
        },
        authorize_payload={
            "url": "https://auth.example/openai",
            "instructions": "Enter OPENAI-CONTRACT-CODE.",
        },
    )

    assert "data:image/png;base64," in page
    assert _style_block(page) == _shared_page_style()
    assert "Open OpenAI authorization" in page
    assert "Copy this code." in page
    assert "Enter OPENAI-CONTRACT-CODE." not in page
    assert '<section class="auth-card" data-device-code-card>' in page
    assert '<div class="actions" data-openai-auth-button>' in page
    assert 'data-copy-code="OPENAI-CONTRACT-CODE"' in page
    assert 'aria-label="Copy device code"' in page


def test_login_page_uses_repository_lfs_brand_asset() -> None:
    asset = resources.files(
        "assistant_api.container_builder.container_plugin.openai_provider_login_plugin"
    ).joinpath("assets", "petprojectcofounder_logo_small.PNG")
    repo_root = Path(__file__).resolve().parents[7]
    asset_path = (
        "src/assistant_api/container_builder/container_plugin/"
        "openai_provider_login_plugin/assets/petprojectcofounder_logo_small.PNG"
    )

    assert asset.is_file()
    assert asset.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert f"{asset_path} filter=lfs diff=lfs merge=lfs -text" in repo_root.joinpath(
        ".gitattributes"
    ).read_text(encoding="utf-8")


def test_automatic_headless_callback_completion_reports_authenticated(
    openai_provider_env: OpenAIProviderEnv,
    opencode_provider_stub: OpenCodeProviderStub,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    plugin = service_class()(
        host_port=openai_provider_env.openai_auth_port,
        opencode_model="openai/gpt-5.5-fast",
    )
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[OpenCodeRuntimeStatePlugin(openai_provider_env.opencode_api_port), plugin]
    )._prepare_specs()
    start_plugin(plugin, container_spec.state)

    login = read_url(service_url("/login", port=openai_provider_env.openai_auth_port))
    automatic_completion = read_url(
        service_url("/login?complete=1", port=openai_provider_env.openai_auth_port)
    )
    status = wait_for_status_state("authenticated", port=openai_provider_env.openai_auth_port)

    assert login.status == 200
    assert automatic_completion.status == 200
    assert "Authorization successful" in automatic_completion.text
    assert '<section class="auth-card" data-device-code-card>' not in automatic_completion.text
    assert '<div class="actions" data-openai-auth-button>' not in automatic_completion.text
    assert "Open OpenAI authorization" not in automatic_completion.text
    assert "OpenAI device code" not in automatic_completion.text
    assert "Copy this code." not in automatic_completion.text
    assert "OPENAI-CONTRACT-CODE" not in automatic_completion.text
    assert status["authValid"] is True
    assert opencode_provider_stub.state.callback_requests == [{"method": 1}]
    config = json.loads(
        tmp_path.joinpath("config", "opencode", "opencode.json").read_text(
            encoding="utf-8"
        )
    )
    assert config["model"] == "openai/gpt-5.5-fast"


def _style_block(page_html: str) -> str:
    return page_html.split("<style>\n", 1)[1].split("\n  </style>", 1)[0]


def _shared_page_style() -> str:
    return (
        resources.files("assistant_api.container_builder.container_plugin")
        .joinpath("assets", SHARED_STYLE_ASSET_NAME)
        .read_text(encoding="utf-8")
    )


def test_repeated_login_while_auth_is_pending_does_not_duplicate_authorize_calls(
    openai_provider_env: OpenAIProviderEnv,
    opencode_provider_stub: OpenCodeProviderStub,
) -> None:
    opencode_provider_stub.state.callback_connects_auth = False
    plugin = service_class()(host_port=openai_provider_env.openai_auth_port)
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[OpenCodeRuntimeStatePlugin(openai_provider_env.opencode_api_port), plugin]
    )._prepare_specs()
    start_plugin(plugin, container_spec.state)

    first_login = read_url(service_url("/login", port=openai_provider_env.openai_auth_port))
    second_login = read_url(service_url("/login", port=openai_provider_env.openai_auth_port))

    assert first_login.status == 200
    assert second_login.status == 200
    assert opencode_provider_stub.state.authorize_requests == [{"method": 1}]


def test_login_when_already_authenticated_does_not_restart_oauth(
    openai_provider_env: OpenAIProviderEnv,
    opencode_provider_stub: OpenCodeProviderStub,
) -> None:
    opencode_provider_stub.state.connected = True
    plugin = service_class()(host_port=openai_provider_env.openai_auth_port)
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[OpenCodeRuntimeStatePlugin(openai_provider_env.opencode_api_port), plugin]
    )._prepare_specs()
    start_plugin(plugin, container_spec.state)

    login = read_url(service_url("/login", port=openai_provider_env.openai_auth_port))
    status = status_json(port=openai_provider_env.openai_auth_port)

    assert login.status == 200
    assert '<section class="auth-card" data-device-code-card>' not in login.text
    assert '<div class="actions" data-openai-auth-button>' not in login.text
    assert "Open OpenAI authorization" not in login.text
    assert "OpenAI device code" not in login.text
    assert "Copy this code." not in login.text
    assert status["state"] == "authenticated"
    assert opencode_provider_stub.state.authorize_requests == []
    assert opencode_provider_stub.state.callback_requests == []


def test_authorize_failure_sets_error_without_fallback_to_other_auth_methods(
    openai_provider_env: OpenAIProviderEnv,
    opencode_provider_stub: OpenCodeProviderStub,
) -> None:
    opencode_provider_stub.state.authorize_failure = True
    plugin = service_class()(host_port=openai_provider_env.openai_auth_port)
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[OpenCodeRuntimeStatePlugin(openai_provider_env.opencode_api_port), plugin]
    )._prepare_specs()
    start_plugin(plugin, container_spec.state)

    login = read_url(service_url("/login", port=openai_provider_env.openai_auth_port))
    status = wait_for_status_state("error", port=openai_provider_env.openai_auth_port)

    assert login.status >= 500
    assert status["authValid"] is False
    assert opencode_provider_stub.state.authorize_requests == [{"method": 1}]
    assert opencode_provider_stub.state.callback_requests == []


def test_callback_failure_sets_error_without_redirect_or_api_key_fallback(
    openai_provider_env: OpenAIProviderEnv,
    opencode_provider_stub: OpenCodeProviderStub,
) -> None:
    opencode_provider_stub.state.callback_failure = True
    plugin = service_class()(host_port=openai_provider_env.openai_auth_port)
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[OpenCodeRuntimeStatePlugin(openai_provider_env.opencode_api_port), plugin]
    )._prepare_specs()
    start_plugin(plugin, container_spec.state)

    login = read_url(service_url("/login", port=openai_provider_env.openai_auth_port))
    complete = read_url(
        service_url("/login?complete=1", port=openai_provider_env.openai_auth_port)
    )
    status = wait_for_status_state("error", port=openai_provider_env.openai_auth_port)

    assert login.status == 200
    assert complete.status >= 500
    assert status["authValid"] is False
    assert opencode_provider_stub.state.authorize_requests == [{"method": 1}]
    assert opencode_provider_stub.state.callback_requests == [{"method": 1}]
