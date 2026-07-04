from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


OPENCODE_CONFIG_SCHEMA = "https://opencode.ai/config.json"
PROVIDER_ID = "openai"


class OpenAIProviderApiTokenError(RuntimeError):
    pass


def install_api_token_auth(
    *,
    api_token_env_var: str,
    opencode_model: str,
    replace_existing: bool,
) -> None:
    token = _api_token_from_env(api_token_env_var)
    auth_path = _opencode_auth_path()
    auth = _read_json_object(auth_path, "OpenCode auth")

    existing = auth.get(PROVIDER_ID)
    if existing is not None and not _is_same_api_auth(existing, token):
        if not replace_existing:
            raise OpenAIProviderApiTokenError(
                "Existing OpenCode openai auth credential conflicts with API-token auth"
            )

    auth[PROVIDER_ID] = {"type": "api", "key": token}
    _write_json_object(auth_path, auth)

    _configure_default_model(opencode_model)


def validate_api_token_auth(
    *,
    api_token_env_var: str,
    opencode_model: str,
    opencode_api_port: int | None,
) -> None:
    token = _api_token_from_env(api_token_env_var)
    auth_path = _opencode_auth_path()
    auth = _read_json_object(auth_path, "OpenCode auth")
    credential = auth.get(PROVIDER_ID)
    if not isinstance(credential, dict):
        raise OpenAIProviderApiTokenError("OpenCode openai auth credential is missing")
    if credential.get("type") != "api":
        raise OpenAIProviderApiTokenError("OpenCode openai auth credential is not API-token auth")
    if credential.get("key") != token:
        raise OpenAIProviderApiTokenError(
            "OpenCode openai auth credential does not match configured API token"
        )

    config = _read_json_object(_opencode_config_path(), "OpenCode config")
    if config.get("model") != opencode_model:
        raise OpenAIProviderApiTokenError("OpenCode default model is not configured")

    if opencode_api_port is not None:
        _wait_for_openai_connected_provider(opencode_api_port)


def validate_openai_opencode_model(model: object) -> str:
    if not isinstance(model, str):
        raise OpenAIProviderApiTokenError("opencode_model must be an OpenCode OpenAI model name")
    if model != model.strip() or not model:
        raise OpenAIProviderApiTokenError(
            "opencode_model must be a non-empty OpenCode OpenAI model name"
        )
    if not model.startswith("openai/") or model == "openai/":
        raise OpenAIProviderApiTokenError(
            "opencode_model must use OpenCode provider/model format"
        )
    return model


def _api_token_from_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value:
        raise OpenAIProviderApiTokenError(f"{name} must contain an OpenAI API token")
    if value != value.strip():
        raise OpenAIProviderApiTokenError(f"{name} must not contain surrounding whitespace")
    return value


def _configure_default_model(model: str) -> None:
    validated_model = validate_openai_opencode_model(model)
    config_path = _opencode_config_path()
    config = _read_json_object(config_path, "OpenCode config")
    config["$schema"] = str(config.get("$schema") or OPENCODE_CONFIG_SCHEMA)
    config["model"] = validated_model
    _write_json_object(config_path, config)


def _opencode_auth_path() -> Path:
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return Path(xdg_data_home) / "opencode" / "auth.json"
    home = os.environ.get("HOME")
    if home:
        return Path(home) / ".local" / "share" / "opencode" / "auth.json"
    raise OpenAIProviderApiTokenError("HOME or XDG_DATA_HOME is required")


def _opencode_config_path() -> Path:
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home) / "opencode" / "opencode.json"
    home = os.environ.get("HOME")
    if home:
        return Path(home) / ".config" / "opencode" / "opencode.json"
    raise OpenAIProviderApiTokenError("HOME or XDG_CONFIG_HOME is required")


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise OpenAIProviderApiTokenError(f"{label} has invalid JSON: {path}") from error
    if not isinstance(payload, dict):
        raise OpenAIProviderApiTokenError(f"{label} must be a JSON object: {path}")
    return payload


def _write_json_object(path: Path, payload: dict[str, Any]) -> None:
    target = _write_target(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    try:
        temp_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temp_path.chmod(0o600)
        os.replace(temp_path, target)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _write_target(path: Path) -> Path:
    if path.is_symlink():
        return path.resolve(strict=False)
    return path


def _is_same_api_auth(value: object, token: str) -> bool:
    return isinstance(value, dict) and value.get("type") == "api" and value.get("key") == token


def _wait_for_openai_connected_provider(port: int) -> None:
    url = f"http://127.0.0.1:{port}/provider"
    deadline = time.monotonic() + 60
    last_error = "provider API was not queried"
    while time.monotonic() < deadline:
        try:
            payload = _read_json_url(url)
        except Exception as error:
            last_error = str(error)
            time.sleep(1)
            continue

        if _provider_is_connected(payload):
            return
        last_error = "OpenCode provider list does not include connected openai provider"
        time.sleep(1)

    raise OpenAIProviderApiTokenError(f"OpenCode provider API is not authenticated: {last_error}")


def _read_json_url(url: str) -> object:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise OpenAIProviderApiTokenError(
            f"OpenCode provider API returned HTTP {error.code}: {body[:500]}"
        ) from error
    try:
        return json.loads(body)
    except json.JSONDecodeError as error:
        raise OpenAIProviderApiTokenError("OpenCode provider API returned invalid JSON") from error


def _provider_is_connected(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    connected = payload.get("connected")
    if isinstance(connected, list) and PROVIDER_ID in connected:
        return True
    providers = payload.get("all")
    if not isinstance(providers, list):
        return False
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        if provider.get("id") == PROVIDER_ID and provider.get("source") in {"api", "env"}:
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser("install")
    _add_common_args(install_parser)
    install_parser.add_argument("--replace-existing", action="store_true")

    health_parser = subparsers.add_parser("health")
    _add_common_args(health_parser)
    health_parser.add_argument("--opencode-api-port", type=int)

    args = parser.parse_args()
    try:
        if args.command == "install":
            install_api_token_auth(
                api_token_env_var=args.api_token_env_var,
                opencode_model=args.opencode_model,
                replace_existing=args.replace_existing,
            )
        elif args.command == "health":
            validate_api_token_auth(
                api_token_env_var=args.api_token_env_var,
                opencode_model=args.opencode_model,
                opencode_api_port=args.opencode_api_port,
            )
    except OpenAIProviderApiTokenError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--api-token-env-var", required=True)
    parser.add_argument("--opencode-model", required=True)


if __name__ == "__main__":
    main()
