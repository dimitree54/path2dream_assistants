from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
from pathlib import Path
from typing import Any


OPENCODE_CONFIG_SCHEMA = "https://opencode.ai/config.json"
PROVIDER_ID = "openai"


class OpenAIProviderAuthRotationError(RuntimeError):
    pass


def install_rotated_auth(
    *,
    candidate_auth_files: list[Path],
    fallback_api_token_env_var: str,
    opencode_model: str,
    probe_model: str,
    probe_variant: str,
    probe_message: str,
    probe_expected_text: str | None,
    probe_timeout_seconds: int,
    working_dir: Path,
) -> None:
    if not candidate_auth_files:
        raise OpenAIProviderAuthRotationError("candidate_auth_files must not be empty")
    _validate_probe_inputs(
        opencode_model=opencode_model,
        probe_model=probe_model,
        probe_variant=probe_variant,
        probe_message=probe_message,
        probe_expected_text=probe_expected_text,
        probe_timeout_seconds=probe_timeout_seconds,
    )
    working_dir.mkdir(parents=True, exist_ok=True)
    active_auth_path = _opencode_auth_path()
    original_active_auth = _read_json_object(active_auth_path, "OpenCode auth")
    _configure_default_model(opencode_model)

    failures: list[str] = []
    for index in _candidate_order(len(candidate_auth_files)):
        candidate_path = candidate_auth_files[index]
        try:
            candidate_auth = _read_json_object(candidate_path, "candidate auth")
            credential = _validated_openai_credential(candidate_auth, "candidate auth")
            _write_active_openai_credential(active_auth_path, credential)
            _probe_active_auth(
                probe_model=probe_model,
                probe_variant=probe_variant,
                probe_message=probe_message,
                probe_expected_text=probe_expected_text,
                probe_timeout_seconds=probe_timeout_seconds,
                working_dir=working_dir,
            )
            return
        except Exception as error:
            failures.append(f"candidate {index}: {_safe_error(error)}")

    try:
        fallback_credential = {
            "type": "api",
            "key": _api_token_from_env(fallback_api_token_env_var),
        }
        _write_active_openai_credential(active_auth_path, fallback_credential)
        _probe_active_auth(
            probe_model=probe_model,
            probe_variant=probe_variant,
            probe_message=probe_message,
            probe_expected_text=probe_expected_text,
            probe_timeout_seconds=probe_timeout_seconds,
            working_dir=working_dir,
        )
        return
    except Exception as error:
        failures.append(f"fallback: {_safe_error(error)}")
        _write_json_object(active_auth_path, original_active_auth)
        raise OpenAIProviderAuthRotationError(
            "No OpenAI provider auth candidate or fallback API token passed probe: "
            + "; ".join(failures)
        ) from error


def validate_candidate_auth_file(path: Path) -> None:
    payload = _read_json_object(path, "candidate auth")
    _validated_openai_credential(payload, "candidate auth")


def validate_openai_opencode_model(model: object, *, name: str = "opencode_model") -> str:
    if not isinstance(model, str):
        raise OpenAIProviderAuthRotationError(f"{name} must be an OpenCode OpenAI model name")
    if model != model.strip() or not model:
        raise OpenAIProviderAuthRotationError(
            f"{name} must be a non-empty OpenCode OpenAI model name"
        )
    if not model.startswith("openai/") or model == "openai/":
        raise OpenAIProviderAuthRotationError(f"{name} must use OpenCode provider/model format")
    return model


def _validate_probe_inputs(
    *,
    opencode_model: str,
    probe_model: str,
    probe_variant: str,
    probe_message: str,
    probe_expected_text: str | None,
    probe_timeout_seconds: int,
) -> None:
    validate_openai_opencode_model(opencode_model)
    validate_openai_opencode_model(probe_model, name="probe_model")
    _validate_non_empty_clean_string(probe_variant, "probe_variant")
    _validate_non_empty_clean_string(probe_message, "probe_message")
    if probe_expected_text is not None:
        _validate_non_empty_clean_string(probe_expected_text, "probe_expected_text")
    if not isinstance(probe_timeout_seconds, int) or probe_timeout_seconds < 1:
        raise OpenAIProviderAuthRotationError("probe_timeout_seconds must be a positive integer")


def _validate_non_empty_clean_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise OpenAIProviderAuthRotationError(f"{name} must be a non-empty string")
    return value


def _candidate_order(count: int) -> list[int]:
    order = list(range(count))
    random.SystemRandom().shuffle(order)
    return order


def _validated_openai_credential(auth: dict[str, Any], label: str) -> dict[str, Any]:
    credential = auth.get(PROVIDER_ID)
    if not isinstance(credential, dict):
        raise OpenAIProviderAuthRotationError(f"{label} must contain openai auth")
    credential_type = credential.get("type")
    if credential_type == "api":
        if not isinstance(credential.get("key"), str) or not credential["key"]:
            raise OpenAIProviderAuthRotationError(f"{label} openai API auth must contain key")
        return dict(credential)
    if credential_type == "oauth":
        for key in ("refresh", "access"):
            if not isinstance(credential.get(key), str) or not credential[key]:
                raise OpenAIProviderAuthRotationError(
                    f"{label} openai OAuth auth must contain {key}"
                )
        expires = credential.get("expires")
        if not isinstance(expires, int) or expires < 0:
            raise OpenAIProviderAuthRotationError(
                f"{label} openai OAuth auth must contain expires"
            )
        return dict(credential)
    raise OpenAIProviderAuthRotationError(f"{label} openai auth must be api or oauth")


def _write_active_openai_credential(
    active_auth_path: Path,
    credential: dict[str, Any],
) -> None:
    active_auth = _read_json_object(active_auth_path, "OpenCode auth")
    active_auth[PROVIDER_ID] = credential
    _write_json_object(active_auth_path, active_auth)


def _probe_active_auth(
    *,
    probe_model: str,
    probe_variant: str,
    probe_message: str,
    probe_expected_text: str | None,
    probe_timeout_seconds: int,
    working_dir: Path,
) -> None:
    command = [
        "opencode",
        "run",
        "--dir",
        str(working_dir),
        "--model",
        probe_model,
        "--variant",
        probe_variant,
        probe_message,
    ]
    try:
        result = subprocess.run(
            command,
            cwd=working_dir,
            check=False,
            capture_output=True,
            text=True,
            timeout=probe_timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        raise OpenAIProviderAuthRotationError("probe timed out") from error
    if result.returncode != 0:
        raise OpenAIProviderAuthRotationError(f"probe exited with code {result.returncode}")
    output = result.stdout + result.stderr
    if probe_expected_text is not None and probe_expected_text not in output:
        raise OpenAIProviderAuthRotationError("probe output did not contain expected text")
    if probe_expected_text is None and not output.strip():
        raise OpenAIProviderAuthRotationError("probe output was empty")


def _configure_default_model(model: str) -> None:
    validated_model = validate_openai_opencode_model(model)
    config_path = _opencode_config_path()
    config = _read_json_object(config_path, "OpenCode config")
    config["$schema"] = str(config.get("$schema") or OPENCODE_CONFIG_SCHEMA)
    config["model"] = validated_model
    _write_json_object(config_path, config)


def _api_token_from_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value:
        raise OpenAIProviderAuthRotationError(f"{name} must contain an OpenAI API token")
    if value != value.strip():
        raise OpenAIProviderAuthRotationError(f"{name} must not contain surrounding whitespace")
    return value


def _opencode_auth_path() -> Path:
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return Path(xdg_data_home) / "opencode" / "auth.json"
    home = os.environ.get("HOME")
    if home:
        return Path(home) / ".local" / "share" / "opencode" / "auth.json"
    raise OpenAIProviderAuthRotationError("HOME or XDG_DATA_HOME is required")


def _opencode_config_path() -> Path:
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home) / "opencode" / "opencode.json"
    home = os.environ.get("HOME")
    if home:
        return Path(home) / ".config" / "opencode" / "opencode.json"
    raise OpenAIProviderAuthRotationError("HOME or XDG_CONFIG_HOME is required")


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise OpenAIProviderAuthRotationError(f"{label} has invalid JSON: {path}") from error
    if not isinstance(payload, dict):
        raise OpenAIProviderAuthRotationError(f"{label} must be a JSON object: {path}")
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


def _safe_error(error: BaseException) -> str:
    if isinstance(error, OpenAIProviderAuthRotationError):
        return str(error)
    return type(error).__name__


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-auth-file", action="append", required=True)
    parser.add_argument("--fallback-api-token-env-var", required=True)
    parser.add_argument("--opencode-model", required=True)
    parser.add_argument("--probe-model", required=True)
    parser.add_argument("--probe-variant", required=True)
    parser.add_argument("--probe-message", required=True)
    parser.add_argument("--probe-expected-text")
    parser.add_argument("--probe-timeout-seconds", required=True, type=int)
    parser.add_argument("--working-dir", required=True)
    args = parser.parse_args()

    try:
        install_rotated_auth(
            candidate_auth_files=[Path(value) for value in args.candidate_auth_file],
            fallback_api_token_env_var=args.fallback_api_token_env_var,
            opencode_model=args.opencode_model,
            probe_model=args.probe_model,
            probe_variant=args.probe_variant,
            probe_message=args.probe_message,
            probe_expected_text=args.probe_expected_text,
            probe_timeout_seconds=args.probe_timeout_seconds,
            working_dir=Path(args.working_dir),
        )
    except OpenAIProviderAuthRotationError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
