from __future__ import annotations

import json
import shlex

from assistant_api.models import ImageSpec


def render_dockerfile(image_spec: ImageSpec) -> str:
    lines = [f"FROM {image_spec.base_image}", "ENTRYPOINT []"]

    for key, value in image_spec.env.items():
        lines.append(f"ENV {key}={json.dumps(value)}")
    apk_packages = _deduplicate(image_spec.apk_packages)
    if apk_packages:
        lines.append("RUN " + shlex.join(["apk", "add", "--no-cache", *apk_packages]))
    python_packages = _deduplicate(image_spec.python_packages)
    if python_packages:
        lines.append(
            "RUN "
            + shlex.join(
                [
                    "python3",
                    "-m",
                    "pip",
                    "install",
                    "--break-system-packages",
                    *python_packages,
                ]
            )
        )
    for command in image_spec.run_commands:
        lines.append(f"RUN {command}")
    if image_spec.workdir is not None:
        lines.append(f"WORKDIR {image_spec.workdir}")
    if image_spec.command is not None:
        lines.append(f"CMD {json.dumps(image_spec.command)}")

    return "\n".join(lines) + "\n"


def _deduplicate(values: list[str]) -> list[str]:
    deduplicated: list[str] = []
    for value in values:
        if value in deduplicated:
            continue
        deduplicated.append(value)
    return deduplicated
