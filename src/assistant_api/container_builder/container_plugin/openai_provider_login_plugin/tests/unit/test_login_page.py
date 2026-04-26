from __future__ import annotations

from assistant_api.container_builder.container_plugin.openai_provider_login_plugin._login_page import (
    render_login_page,
)


def test_pending_login_page_renders_authorization_ui_and_hide_hooks() -> None:
    page = render_login_page(
        provider_name="OpenAI",
        status={
            "authValid": False,
            "state": "unauthenticated",
            "message": "OpenAI provider is not authenticated.",
        },
        authorize_payload={
            "url": "https://auth.example/openai",
            "instructions": "Enter code: OPENAI-CONTRACT-CODE.",
        },
    )

    assert '<section class="auth-card" data-device-code-card>' in page
    assert '<div class="actions" data-openai-auth-button>' in page
    assert 'setAuthorizationControlsHidden(authenticated)' in page
    assert 'deviceCodeCard.hidden = hidden;' in page
    assert 'openaiAuthButton.hidden = hidden;' in page
    assert "Open OpenAI authorization" in page
    assert "OpenAI device code" in page
    assert "Copy this code." in page
    assert "OPENAI-CONTRACT-CODE" in page


def test_authenticated_login_page_omits_authorization_ui_even_with_payload() -> None:
    page = render_login_page(
        provider_name="OpenAI",
        status={
            "authValid": True,
            "state": "authenticated",
            "message": "OpenAI provider is authenticated.",
        },
        authorize_payload={
            "url": "https://auth.example/openai",
            "instructions": "Enter code: OPENAI-CONTRACT-CODE.",
        },
    )

    assert "Authorization successful" in page
    assert '<section class="auth-card" data-device-code-card>' not in page
    assert '<div class="actions" data-openai-auth-button>' not in page
    assert "Open OpenAI authorization" not in page
    assert "OpenAI device code" not in page
    assert "Copy this code." not in page
    assert "OPENAI-CONTRACT-CODE" not in page
