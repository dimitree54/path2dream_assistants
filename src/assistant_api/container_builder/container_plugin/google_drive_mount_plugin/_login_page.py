from __future__ import annotations

import base64
import html
from importlib import resources
from pathlib import Path

if __package__:
    from ._local_folder_import_control import render_local_folder_import_control
else:
    from _local_folder_import_control import render_local_folder_import_control


LOGO_ASSET_NAME = "petprojectcofounder_logo_small.PNG"
SHARED_STYLE_ASSET_NAME = "petprojectcofounder_login_page.css"


def render_login_page(authorize_url: str, folder_name: str) -> str:
    safe_authorize_url = html.escape(authorize_url, quote=True)
    safe_folder_name = html.escape(folder_name)
    return _render_page(
        title="Connect Google Drive | Pet Project Cofounder",
        content=f"""
      <p class="eyebrow">Secure Google Drive Mount</p>
      <h1 id="page-title">Connect your Drive</h1>
      <p>Authorize access so Notes Assistant can create and mount a dedicated app folder in Google Drive. Your files stay visible and manageable in your Drive.</p>
      <div class="folder" title="Google Drive folder">Folder: {safe_folder_name}</div>
      <div class="actions">
        <a class="button" href="{safe_authorize_url}" rel="noreferrer">Authorize Google Drive</a>
      </div>
      <p class="note">The app requests the minimal Drive file scope needed for this folder.</p>""",
    )


def render_mount_success_page(folder_name: str, enable_local_folder_import: bool = False) -> str:
    safe_folder_name = html.escape(folder_name)
    import_control = render_local_folder_import_control() if enable_local_folder_import else ""
    return _render_page(
        title="Google Drive Connected | Pet Project Cofounder",
        content=f"""
      <p class="eyebrow">Secure Google Drive Mount</p>
      <h1 id="page-title">Drive connected</h1>
      <p>Google Drive is mounted successfully. You can return to the Assistant and continue using it.</p>
      <div class="folder" title="Google Drive folder">Folder: {safe_folder_name}</div>
      <section class="status-card" data-auth-status="authenticated" aria-live="polite">
        <span class="dot" aria-hidden="true"></span>
        <div>
          <span class="label">Mount status</span>
          <h2>Google Drive is mounted</h2>
          <p class="success">Everything is ready. Proceed to using the Assistant.</p>
        </div>
      </section>
{import_control}
      <div class="actions">
        <a class="button" href="/logout">Log out</a>
      </div>
      <p class="note">The mounted folder remains visible and manageable in Google Drive.</p>""",
    )


def _render_page(*, title: str, content: str) -> str:
    logo_src = _logo_data_uri()
    page_style = _shared_page_style()
    safe_title = html.escape(title)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
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
{content}
    </section>
  </main>
</body>
</html>"""


def _logo_data_uri() -> str:
    if __package__:
        logo = resources.files(__package__).joinpath("assets", LOGO_ASSET_NAME).read_bytes()
    else:
        logo = _standalone_asset_path(LOGO_ASSET_NAME).read_bytes()
    encoded = base64.b64encode(logo).decode("ascii")
    return f"data:image/png;base64,{encoded}"


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
