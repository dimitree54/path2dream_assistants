from __future__ import annotations

import json
import os
from dataclasses import dataclass

from assistant_api.container_builder._errors import ConfigurationError


@dataclass(slots=True)
class OAuthCredentials:
    client_id: str
    client_secret: str


def credentials_from_env() -> OAuthCredentials:
    raw = _required_env("GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ConfigurationError("GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON must be valid JSON") from error
    web = payload.get("web") if isinstance(payload, dict) else None
    if not isinstance(web, dict):
        raise ConfigurationError("GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON must describe a Web client")
    client_id = web.get("client_id")
    client_secret = web.get("client_secret")
    if not isinstance(client_id, str) or not client_id:
        raise ConfigurationError("GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON must contain web.client_id")
    if not isinstance(client_secret, str) or not client_secret:
        raise ConfigurationError("GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON must contain web.client_secret")
    return OAuthCredentials(client_id=client_id, client_secret=client_secret)


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigurationError(f"{name} is required")
    return value
