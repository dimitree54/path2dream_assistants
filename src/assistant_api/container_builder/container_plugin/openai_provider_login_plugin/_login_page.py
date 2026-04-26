from __future__ import annotations

import base64
import html
import re
from importlib import resources
from pathlib import Path
from typing import Any

LOGO_ASSET_NAME = "petprojectcofounder_logo_small.PNG"
SHARED_STYLE_ASSET_NAME = "petprojectcofounder_login_page.css"
PENDING_STATUS_MESSAGE = "Use the button above to open OpenAI authorization, enter the device code, and finish the flow. This page will update automatically."

def render_login_page(
    *,
    provider_name: str,
    status: dict[str, Any],
    authorize_payload: dict[str, Any] | None,
) -> str:
    logo_src = _logo_data_uri()
    page_style = _shared_page_style()
    safe_provider_name = html.escape(provider_name)
    state = str(status.get("state", "unauthenticated"))
    auth_valid = bool(status.get("authValid")) or state == "authenticated"
    raw_status_message = str(status.get("message") or "")
    status_kind = "authenticated" if auth_valid else state
    status_title = "Authorization successful" if auth_valid else "Waiting for OpenAI authorization"
    status_message = (
        "OpenAI is connected. You can return to the bot."
        if auth_valid
        else raw_status_message if state in {"error", "unavailable"} else PENDING_STATUS_MESSAGE
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Connect OpenAI | Pet Project Cofounder</title>
  <style>
{page_style}
  </style>
</head>
<body>
  <main aria-labelledby="page-title">
    <section class="brand" aria-label="Pet Project Cofounder branding">
      <img src="{logo_src}" alt="Pet Project Cofounder rocket cat logo">
      <strong>Pet Project Cofounder</strong>
    </section>
    <section class="content">
      <p class="eyebrow">Secure {safe_provider_name} Login</p>
      <h1 id="page-title">Connect OpenAI</h1>
      <p>Authorize the OpenAI provider used by OpenCode. Keep this page open after entering the code; it checks completion automatically and switches to success when authorization is ready.</p>
      <div class="folder" title="OpenCode provider">Provider: {safe_provider_name}</div>
      {_authorization_markup(authorize_payload, auth_valid)}
      <section class="status-card" data-auth-status="{html.escape(status_kind, quote=True)}" aria-live="polite">
        <span class="dot" aria-hidden="true"></span>
        <div>
          <span class="label">Live status</span>
          <h2 data-status-title>{html.escape(status_title)}</h2>
          <p data-status-message>{html.escape(status_message)}</p>
          <p class="success" data-success-message {'' if auth_valid else 'hidden'}>Authorization successful. You can return to the bot.</p>
        </div>
      </section>
    </section>
  </main>
  <script>
    (() => {{
      const pendingStatusMessage = "{html.escape(PENDING_STATUS_MESSAGE)}";
      const statusCard = document.querySelector("[data-auth-status]"), statusTitle = document.querySelector("[data-status-title]"), statusMessage = document.querySelector("[data-status-message]"), successMessage = document.querySelector("[data-success-message]"), deviceCodeCard = document.querySelector("[data-device-code-card]"), openaiAuthButton = document.querySelector("[data-openai-auth-button]"), copyButton = document.querySelector("[data-copy-code]"), copyFeedback = document.querySelector("[data-copy-feedback]");
      const wait = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));
      if (copyButton && copyFeedback) {{
        copyButton.addEventListener("click", async () => {{
          const code = copyButton.dataset.copyCode;
          try {{
            if (!navigator.clipboard) throw new Error("Clipboard API is unavailable.");
            await navigator.clipboard.writeText(code);
            copyFeedback.textContent = "Code copied.";
          }} catch (_error) {{
            copyFeedback.textContent = "Unable to copy automatically.";
          }}
        }});
      }}
      function setAuthorizationControlsHidden(hidden) {{
        if (deviceCodeCard) deviceCodeCard.hidden = hidden;
        if (openaiAuthButton) openaiAuthButton.hidden = hidden;
      }}
      function renderStatus(status) {{
        const state = status.state || "unauthenticated";
        const authenticated = Boolean(status.authValid) || state === "authenticated";
        statusCard.dataset.authStatus = authenticated ? "authenticated" : state;
        statusTitle.textContent = authenticated
          ? "Authorization successful"
          : state === "error" || state === "unavailable"
            ? "Authorization check needs attention"
            : "Waiting for OpenAI authorization";
        statusMessage.textContent = authenticated
          ? "OpenAI is connected. You can return to the bot."
          : state === "error" || state === "unavailable"
            ? status.message || "Unable to check authorization."
            : pendingStatusMessage;
        setAuthorizationControlsHidden(authenticated);
        successMessage.hidden = !authenticated;
        return authenticated;
      }}
      async function readStatus() {{
        return (await fetch("/status", {{ cache: "no-store" }})).json();
      }}
      async function monitorAuthorization() {{
        while (true) {{
          const before = await readStatus();
          if (renderStatus(before) || before.state === "error") return;
          if (before.state === "unavailable") {{
            await wait(1800);
            continue;
          }}
          await fetch("/login?complete=1", {{ cache: "no-store" }});
          const after = await readStatus();
          if (renderStatus(after) || after.state === "error") return;
          await wait(1800);
        }}
      }}
      monitorAuthorization().catch((error) => {{
        statusCard.dataset.authStatus = "error";
        statusTitle.textContent = "Authorization check failed";
        statusMessage.textContent = error instanceof Error ? error.message : "Unable to check authorization.";
      }});
    }})();
  </script>
</body>
</html>"""

def _authorization_markup(authorize_payload: dict[str, Any] | None, auth_valid: bool) -> str:
    if auth_valid:
        return ""
    if authorize_payload is None:
        return "<p class=\"note\">OpenAI authorization has not started yet.</p>"
    url = authorize_payload.get("url")
    instructions = authorize_payload.get("instructions")
    if not isinstance(url, str) or not url:
        raise ValueError("OpenAI authorize response did not include url")
    if not isinstance(instructions, str) or not instructions:
        raise ValueError("OpenAI authorize response did not include device code instructions")
    device_code = _extract_device_code(instructions)
    safe_url = html.escape(url, quote=True)
    safe_device_code = html.escape(device_code)
    safe_device_code_attr = html.escape(device_code, quote=True)
    return f"""
      <section class="auth-card" data-device-code-card>
        <span class="label">OpenAI device code</span>
        <p>Copy this code.</p>
        <div class="device-code-box">
          <code class="device-code-field" aria-label="OpenAI device code">{safe_device_code}</code>
          <button class="copy-icon-button" type="button" data-copy-code="{safe_device_code_attr}" aria-label="Copy device code" title="Copy device code"><svg class="copy-icon" aria-hidden="true" viewBox="0 0 24 24"><path d="M8 8h10v12H8zM6 16H4V4h12v2"/></svg></button>
        </div>
        <p class="copy-feedback" data-copy-feedback aria-live="polite"></p>
      </section>
      <div class="actions" data-openai-auth-button>
        <a class="button" href="{safe_url}" target="_blank" rel="noreferrer">Open OpenAI authorization</a>
      </div>"""

def _extract_device_code(instructions: str) -> str:
    for pattern in (
        r"\bcode\s*:\s*([A-Z0-9][A-Z0-9-]{3,})",
        r"\b([A-Z0-9]{2,}(?:-[A-Z0-9]{2,})+)\b",
    ):
        match = re.search(pattern, instructions, re.IGNORECASE)
        if match is not None:
            return match.group(1)
    stripped = instructions.strip()
    if re.fullmatch(r"[A-Z0-9][A-Z0-9-]{3,}", stripped, re.IGNORECASE):
        return stripped
    raise ValueError("OpenAI authorize response did not include a device code")

def _logo_data_uri() -> str:
    return f"data:image/png;base64,{base64.b64encode(_logo_bytes()).decode('ascii')}"

def _logo_bytes() -> bytes:
    if __package__:
        return resources.files(__package__).joinpath("assets", LOGO_ASSET_NAME).read_bytes()
    return _standalone_asset_path(LOGO_ASSET_NAME).read_bytes()

def _shared_page_style() -> str:
    if __package__:
        parent_package = __package__.rsplit(".", 1)[0]
        return (
            resources.files(parent_package)
            .joinpath("assets", SHARED_STYLE_ASSET_NAME)
            .read_text(encoding="utf-8")
        )
    return _standalone_asset_path(SHARED_STYLE_ASSET_NAME).read_text(encoding="utf-8")

def _standalone_asset_path(asset_name: str) -> Path:
    for candidate in (
        Path(__file__).with_name("assets").joinpath(asset_name),
        Path(__file__).parent.parent.joinpath("assets", asset_name),
    ):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(asset_name)
