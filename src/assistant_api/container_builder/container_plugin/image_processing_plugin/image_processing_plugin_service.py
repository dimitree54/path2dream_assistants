from __future__ import annotations

from assistant_api.models import ContainerRuntimeContext, ContainerSpec, ImageSpec


SYSTEM_PACKAGES = [
    "imagemagick",
    "ffmpeg",
    "libwebp-tools",
    "libheif-tools",
    "jpegoptim",
    "optipng",
    "pngquant",
    "python3",
    "py3-pip",
]
PYTHON_PACKAGES = ["pillow", "pillow-heif"]
CLI_COMMANDS = [
    "magick",
    "identify",
    "ffmpeg",
    "cwebp",
    "dwebp",
    "gif2webp",
    "heif-convert",
    "heif-info",
    "jpegoptim",
    "optipng",
    "pngquant",
]


class ImageProcessingPluginService:
    name = "image-processing"

    def configure_image(self, image: ImageSpec) -> None:
        image.apk_packages.extend(SYSTEM_PACKAGES)
        image.python_packages.extend(PYTHON_PACKAGES)

    def configure_container(self, container: ContainerSpec) -> None:
        return None

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        result = runtime.exec(["/bin/sh", "-lc", _health_command()])
        if result.exit_code != 0:
            raise RuntimeError(f"image processing health check failed: {result.output}")


def _health_command() -> str:
    command_checks = [f"command -v {command} >/dev/null" for command in CLI_COMMANDS]
    return "\n".join(
        [
            "set -eu",
            *command_checks,
            "python3 - <<'PY'",
            "import PIL",
            "import pillow_heif",
            "PY",
        ]
    )
