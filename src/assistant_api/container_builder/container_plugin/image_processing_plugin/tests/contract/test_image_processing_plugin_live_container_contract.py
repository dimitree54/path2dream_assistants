from __future__ import annotations

import os
from collections.abc import Iterable

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin.image_processing_plugin import (
    ImageProcessingPluginService,
)


@pytest.mark.live_container
def test_live_container_processes_common_photo_formats() -> None:
    builder = ContainerBuilderService(
        plugins=[ImageProcessingPluginService()],
        container_name=f"notes-assistant-image-processing-test-{os.getpid()}",
    )

    try:
        running = builder.build_and_run()
    except Exception as error:
        _stop_builder_if_started(builder)
        pytest.fail(
            "image processing plugin image must build, start, and pass dependency "
            f"health checks; got {type(error).__name__}: {error}\n\n"
            f"{_docker_build_log(error)}"
        )

    try:
        result = running.container.exec_run(["/bin/sh", "-lc", _media_probe_script()])
        output = _decode_output(result.output)
        assert result.exit_code == 0, output
        assert "image-processing-live-probe-ok" in output
    finally:
        builder.stop(remove=True)


def _media_probe_script() -> str:
    return "\n".join(
        [
            "set -eu",
            "work_dir=$(mktemp -d)",
            "cd \"$work_dir\"",
            "magick -size 80x60 gradient:navy-cyan source.png",
            "test \"$(identify -format '%wx%h' source.png)\" = '80x60'",
            "file source.png | grep -q 'PNG image data'",
            "file source.png | grep -q '80 x 60'",
            "magick source.png -resize 40x30 resized.jpg",
            "test \"$(identify -format '%wx%h' resized.jpg)\" = '40x30'",
            "jpegoptim --quiet resized.jpg",
            "optipng -quiet source.png",
            "pngquant --output quantized.png --force --quality 0-100 source.png",
            "test -s quantized.png",
            "cwebp -quiet source.png -o source.webp",
            "test -s source.webp",
            "dwebp source.webp -o decoded_from_webp.png >/dev/null",
            "test \"$(identify -format '%wx%h' decoded_from_webp.png)\" = '80x60'",
            "magick -size 16x16 xc:red -size 16x16 xc:blue -loop 0 animated.gif",
            "gif2webp -quiet animated.gif -o animated.webp",
            "test -s animated.webp",
            "ffmpeg -hide_banner -loglevel error -y -f lavfi -i testsrc=size=32x24:duration=1 -frames:v 1 ffmpeg_frame.png",
            "test \"$(identify -format '%wx%h' ffmpeg_frame.png)\" = '32x24'",
            "python3 - <<'PY'",
            "from PIL import Image",
            "import pillow_heif",
            "",
            "pillow_heif.register_heif_opener()",
            "image = Image.new('RGB', (24, 18), (30, 120, 200))",
            "image.save('pillow_resized.webp')",
            "Image.open('pillow_resized.webp').resize((12, 9)).save('pillow_resized.jpg')",
            "image.save('phone_photo.heic')",
            "PY",
            "test \"$(identify -format '%wx%h' pillow_resized.jpg)\" = '12x9'",
            "heif-info phone_photo.heic >/dev/null",
            "heif-convert phone_photo.heic phone_photo.jpg >/dev/null",
            "test \"$(identify -format '%wx%h' phone_photo.jpg)\" = '24x18'",
            "printf '%s\\n' image-processing-live-probe-ok",
        ]
    )


def _decode_output(output: bytes | str) -> str:
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output


def _stop_builder_if_started(builder: ContainerBuilderService) -> None:
    try:
        builder.stop(remove=True)
    except Exception:
        return None


def _docker_build_log(error: BaseException) -> str:
    build_log = getattr(error, "build_log", None)
    if not build_log:
        return "<docker build log is not available>"

    lines: list[str] = []
    for entry in _iter_build_log_entries(build_log):
        if isinstance(entry, dict):
            line = entry.get("stream") or entry.get("error") or repr(entry)
        else:
            line = repr(entry)
        lines.append(line.rstrip())
    return "\n".join(lines)


def _iter_build_log_entries(build_log: object) -> Iterable[object]:
    if isinstance(build_log, Iterable):
        return build_log
    return []
