from __future__ import annotations

import json

from assistant_api.models import ImageSpec


def render_dockerfile(image_spec: ImageSpec) -> str:
    lines = [f"FROM {image_spec.base_image}", "ENTRYPOINT []"]

    for key, value in image_spec.env.items():
        lines.append(f"ENV {key}={json.dumps(value)}")
    for command in image_spec.run_commands:
        lines.append(f"RUN {command}")
    if image_spec.workdir is not None:
        lines.append(f"WORKDIR {image_spec.workdir}")
    if image_spec.command is not None:
        lines.append(f"CMD {json.dumps(image_spec.command)}")

    return "\n".join(lines) + "\n"
