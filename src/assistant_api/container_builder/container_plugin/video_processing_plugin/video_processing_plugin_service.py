from __future__ import annotations

import base64
import shlex
from pathlib import Path

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
    "font-dejavu",
    "font-noto-emoji",
    "util-linux",
    "gcompat",
]
PYTHON_PACKAGES = ["pillow", "pillow-heif", "requests", "pyyaml==6.0.3"]
CHROMIUM_EXECUTABLE_PATH = "/usr/bin/chromium-browser"
DEJAVU_LICENSE_PATH = "/usr/share/licenses/font-dejavu/LICENSE"
DEJAVU_LICENSE_SHA256 = "7a083b136e64d064794c3419751e5c7dd10d2f64c108fe5ba161eae5e5958a93"
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
        image.run_commands.append(_dejavu_license_install_command())

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
            "apk info -e font-dejavu >/dev/null",
            "bold_family=$(fc-match -f '%{family}\\n' 'DejaVu Sans:style=Bold' | head -n 1 | cut -d, -f1)",
            "test \"$bold_family\" = 'DejaVu Sans'",
            "condensed_family=$(fc-match -f '%{family}\\n' 'DejaVu Sans Condensed:style=Bold' | head -n 1 | cut -d, -f1)",
            "test \"$condensed_family\" = 'DejaVu Sans'",
            "bold_path=$(fc-match -f '%{file}\\n' 'DejaVu Sans:style=Bold' | head -n 1)",
            "condensed_path=$(fc-match -f '%{file}\\n' 'DejaVu Sans Condensed:style=Bold' | head -n 1)",
            'test -r "$bold_path"',
            'test -r "$condensed_path"',
            'apk info -W "$bold_path" | grep -q "is owned by font-dejavu-"',
            'apk info -W "$condensed_path" | grep -q "is owned by font-dejavu-"',
            f"test \"$(sha256sum {DEJAVU_LICENSE_PATH} | awk '{{print $1}}')\" = '{DEJAVU_LICENSE_SHA256}'",
            "python3 - <<'PY'",
            "import PIL",
            "import pillow_heif",
            "import requests",
            "import yaml",
            "PY",
        ]
    )


def _dejavu_license_install_command() -> str:
    license_file = Path(__file__).with_name("assets") / "DEJAVU_LICENSE.txt"
    encoded_license = base64.b64encode(license_file.read_bytes()).decode("ascii")
    license_dir = str(Path(DEJAVU_LICENSE_PATH).parent)
    return (
        f"mkdir -p {shlex.quote(license_dir)} && "
        f"printf '%s' {shlex.quote(encoded_license)} | "
        f"base64 -d > {shlex.quote(DEJAVU_LICENSE_PATH)}"
    )
