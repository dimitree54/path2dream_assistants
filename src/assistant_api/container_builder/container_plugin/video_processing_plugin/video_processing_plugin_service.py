from __future__ import annotations

from assistant_api.models import ContainerRuntimeContext, ContainerSpec, ImageSpec


SYSTEM_PACKAGES = [
    "ffmpeg",
    "imagemagick",
    "libwebp-tools",
    "libheif-tools",
    "jpegoptim",
    "optipng",
    "pngquant",
    "file",
    "python3",
    "py3-pip",
    "nodejs",
    "npm",
    "chromium",
    "nss",
    "freetype",
    "harfbuzz",
    "ca-certificates",
    "fontconfig",
    "ttf-freefont",
    "font-noto-emoji",
    "util-linux",
    "gcompat",
]
PYTHON_PACKAGES = ["pillow", "pillow-heif", "requests"]
CHROMIUM_EXECUTABLE_PATH = "/usr/bin/chromium-browser"
CLI_COMMANDS = [
    "ffmpeg",
    "ffprobe",
    "node",
    "npm",
    "npx",
    "magick",
    "identify",
    "cwebp",
    "dwebp",
    "gif2webp",
    "heif-convert",
    "heif-info",
    "jpegoptim",
    "optipng",
    "pngquant",
    "file",
    "setpriv",
]


class VideoProcessingPluginService:
    name = "video-processing"

    def configure_image(self, image: ImageSpec) -> None:
        image.apk_packages.extend(SYSTEM_PACKAGES)
        image.python_packages.extend(PYTHON_PACKAGES)
        image.env["CHROMIUM_EXECUTABLE_PATH"] = CHROMIUM_EXECUTABLE_PATH

    def configure_container(self, container: ContainerSpec) -> None:
        container.shm_size = "1g"

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        result = runtime.exec(["/bin/sh", "-lc", _health_command()])
        if result.exit_code != 0:
            raise RuntimeError(f"video processing health check failed: {result.output}")


def _health_command() -> str:
    command_checks = [f"command -v {command} >/dev/null" for command in CLI_COMMANDS]
    return "\n".join(
        [
            "set -eu",
            *command_checks,
            "apk info -e gcompat >/dev/null",
            "container_arch=$(uname -m)",
            'case "$container_arch" in',
            "  aarch64) gcompat_loader=/lib/ld-linux-aarch64.so.1 ;;",
            "  x86_64) gcompat_loader=/lib64/ld-linux-x86-64.so.2 ;;",
            "  *)",
            "    printf 'Unsupported container architecture: %s\\n' \"$container_arch\" >&2",
            "    exit 1",
            "    ;;",
            "esac",
            'test -x "$gcompat_loader"',
            'test -x "$CHROMIUM_EXECUTABLE_PATH"',
            "\"$CHROMIUM_EXECUTABLE_PATH\" --headless --no-sandbox --disable-gpu --dump-dom 'data:text/html,<html>ok</html>' | grep -q ok",
            "fc-list | grep -q .",
            "python3 - <<'PY'",
            "import PIL",
            "import pillow_heif",
            "import requests",
            "PY",
        ]
    )
