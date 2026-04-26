from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


OPENCODE_CONFIG_SCHEMA = "https://opencode.ai/config.json"


class OpenCodeConfigError(RuntimeError):
    pass


def validate_openai_opencode_model(model: object) -> str:
    if not isinstance(model, str):
        raise OpenCodeConfigError("opencode_model must be an OpenCode OpenAI model name")
    if model != model.strip() or not model:
        raise OpenCodeConfigError(
            "opencode_model must be a non-empty OpenCode OpenAI model name"
        )
    if not model.startswith("openai/") or model == "openai/":
        raise OpenCodeConfigError("opencode_model must use OpenCode provider/model format")
    return model


def configure_default_model(model: str) -> Path:
    validated_model = validate_openai_opencode_model(model)
    config_path = _opencode_config_path()
    config = _read_config(config_path)
    config["$schema"] = str(config.get("$schema") or OPENCODE_CONFIG_SCHEMA)
    config["model"] = validated_model
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return config_path


def _opencode_config_path() -> Path:
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home) / "opencode" / "opencode.json"
    home = os.environ.get("HOME")
    if not home:
        raise OpenCodeConfigError("HOME or XDG_CONFIG_HOME is required")
    return Path(home) / ".config" / "opencode" / "opencode.json"


def _read_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise OpenCodeConfigError(f"OpenCode config has invalid JSON: {config_path}") from error
    if not isinstance(config, dict):
        raise OpenCodeConfigError(f"OpenCode config must be a JSON object: {config_path}")
    return config


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("opencode_model argument is required")
    try:
        configure_default_model(sys.argv[1])
    except OpenCodeConfigError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
