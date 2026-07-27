from __future__ import annotations

import os
from collections.abc import Iterable

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin.video_processing_plugin import (
    VideoProcessingPluginService,
)


@pytest.mark.live_container
def test_live_container_supports_video_image_and_remotion_rendering() -> None:
    builder = ContainerBuilderService(
        plugins=[VideoProcessingPluginService()],
        container_name=f"notes-assistant-video-processing-test-{os.getpid()}",
    )

    try:
        running = builder.build_and_run()
    except Exception as error:
        _stop_builder_if_started(builder)
        pytest.fail(
            "video processing plugin image must build, start, and pass dependency "
            f"health checks; got {type(error).__name__}: {error}\n\n"
            f"{_docker_build_log(error)}"
        )

    try:
        assert running.container_spec.shm_size == "1g"
        _run_probe(
            running.container,
            _shared_memory_probe_script(),
            "video-processing-shm-probe-ok",
        )
        _run_probe(running.container, _media_probe_script(), "video-processing-media-probe-ok")
        _run_probe(
            running.container,
            _chromium_probe_script(),
            "video-processing-chromium-probe-ok",
        )
        _run_probe(
            running.container,
            _remotion_probe_script(),
            "video-processing-remotion-probe-ok",
        )
    finally:
        builder.stop(remove=True)


def _run_probe(container: object, script: str, ok_marker: str) -> None:
    result = container.exec_run(["/bin/sh", "-lc", script])
    output = _decode_output(result.output)
    assert result.exit_code == 0, f"probe expecting {ok_marker} failed:\n{output}"
    assert ok_marker in output, output


def _media_probe_script() -> str:
    return "\n".join(
        [
            "set -eu",
            "work_dir=$(mktemp -d)",
            "cd \"$work_dir\"",
            # Video/audio: synthesize, probe, cut, extract frame, resize, split audio.
            "ffmpeg -hide_banner -loglevel error -y -f lavfi -i testsrc=size=64x48:rate=15:duration=2 -f lavfi -i sine=frequency=440:sample_rate=44100:duration=2 -pix_fmt yuv420p -c:v libx264 -c:a aac source.mp4",
            "test \"$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height -of csv=p=0 source.mp4)\" = 'h264,64,48'",
            "ffprobe -v error -select_streams a:0 -show_entries stream=codec_name -of csv=p=0 source.mp4 | grep -q aac",
            "ffmpeg -hide_banner -loglevel error -y -ss 0.5 -t 1 -i source.mp4 -pix_fmt yuv420p -c:v libx264 -c:a aac cut.mp4",
            "cut_duration=$(ffprobe -v error -show_entries format=duration -of csv=p=0 cut.mp4)",
            "awk \"BEGIN {exit !($cut_duration >= 0.8 && $cut_duration <= 1.2)}\"",
            "ffmpeg -hide_banner -loglevel error -y -i source.mp4 -frames:v 1 frame.png",
            "test \"$(identify -format '%wx%h' frame.png)\" = '64x48'",
            "ffmpeg -hide_banner -loglevel error -y -i source.mp4 -vf scale=32:24 -pix_fmt yuv420p -c:v libx264 -an resized.mp4",
            "test \"$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=p=0 resized.mp4)\" = '32,24'",
            "ffmpeg -hide_banner -loglevel error -y -i source.mp4 -vn -c:a copy audio.m4a",
            "ffprobe -v error -select_streams a:0 -show_entries stream=codec_name -of csv=p=0 audio.m4a | grep -q aac",
            # Image superset: ImageMagick, optimizers, WebP, HEIC, file, Pillow.
            "magick -size 80x60 gradient:navy-cyan source.png",
            "test \"$(identify -format '%wx%h' source.png)\" = '80x60'",
            "file source.png | grep -q 'PNG image data'",
            "magick source.png -resize 40x30 resized.jpg",
            "test \"$(identify -format '%wx%h' resized.jpg)\" = '40x30'",
            "jpegoptim --quiet resized.jpg",
            "optipng -quiet source.png",
            "pngquant --output quantized.png --force --quality 0-100 source.png",
            "test -s quantized.png",
            "cwebp -quiet source.png -o source.webp",
            "dwebp source.webp -o decoded_from_webp.png >/dev/null",
            "test \"$(identify -format '%wx%h' decoded_from_webp.png)\" = '80x60'",
            "magick -size 16x16 xc:red -size 16x16 xc:blue -loop 0 animated.gif",
            "gif2webp -quiet animated.gif -o animated.webp",
            "test -s animated.webp",
            "python3 - <<'PY'",
            "from PIL import Image",
            "import pillow_heif",
            "",
            "pillow_heif.register_heif_opener()",
            "image = Image.new('RGB', (24, 18), (30, 120, 200))",
            "image.save('phone_photo.heic')",
            "PY",
            "heif-info phone_photo.heic >/dev/null",
            "heif-convert phone_photo.heic phone_photo.jpg >/dev/null",
            "test \"$(identify -format '%wx%h' phone_photo.jpg)\" = '24x18'",
            "printf '%s\\n' video-processing-media-probe-ok",
        ]
    )


def _shared_memory_probe_script() -> str:
    return "\n".join(
        [
            "set -eu",
            "shm_mb=$(df -m /dev/shm | awk 'NR==2 {print $2}')",
            "awk \"BEGIN {exit !($shm_mb >= 900)}\"",
            "printf '%s\\n' video-processing-shm-probe-ok",
        ]
    )


def _chromium_probe_script() -> str:
    return "\n".join(
        [
            "set -eu",
            "test -n \"$CHROMIUM_EXECUTABLE_PATH\"",
            "test -x \"$CHROMIUM_EXECUTABLE_PATH\"",
            "\"$CHROMIUM_EXECUTABLE_PATH\" --version",
            "fc-list | grep -qi freesans",
            "fc-list | grep -qi 'noto color emoji'",
            "work_dir=$(mktemp -d)",
            "cd \"$work_dir\"",
            "\"$CHROMIUM_EXECUTABLE_PATH\" --headless --no-sandbox --disable-gpu --dump-dom 'data:text/html,<html>ok</html>' | grep -q ok",
            "\"$CHROMIUM_EXECUTABLE_PATH\" --headless --no-sandbox --disable-gpu --hide-scrollbars --window-size=320,240 --screenshot=shot.png about:blank",
            "test \"$(identify -format '%wx%h' shot.png)\" = '320x240'",
            "file shot.png | grep -q 'PNG image data'",
            "printf '%s\\n' video-processing-chromium-probe-ok",
        ]
    )


def _remotion_probe_script() -> str:
    return "\n".join(
        [
            "set -eu",
            "node --version",
            "npm --version",
            "npx --version",
            "work_dir=$(mktemp -d)",
            "cd \"$work_dir\"",
            "mkdir -p src out",
            "cat > package.json <<'JSON'",
            "{",
            "  \"name\": \"video-processing-live-probe\",",
            "  \"version\": \"1.0.0\",",
            "  \"private\": true",
            "}",
            "JSON",
            "cat > src/index.ts <<'TS'",
            "import React from 'react';",
            "import {AbsoluteFill, Composition, registerRoot, useCurrentFrame} from 'remotion';",
            "",
            "const Probe = () => {",
            "  const frame = useCurrentFrame();",
            "  return React.createElement(",
            "    AbsoluteFill,",
            "    {",
            "      style: {",
            "        backgroundColor: '#000080',",
            "        color: 'white',",
            "        fontSize: 24,",
            "        alignItems: 'center',",
            "        justifyContent: 'center',",
            "      },",
            "    },",
            "    React.createElement('div', null, `probe frame ${frame}`),",
            "  );",
            "};",
            "",
            "const Root = () => {",
            "  return React.createElement(Composition, {",
            "    id: 'Probe',",
            "    component: Probe,",
            "    durationInFrames: 1,",
            "    fps: 1,",
            "    width: 160,",
            "    height: 120,",
            "  });",
            "};",
            "",
            "registerRoot(Root);",
            "TS",
            "npm install --no-audit --no-fund --loglevel=error remotion@4.0.484 @remotion/cli@4.0.484 react@18 react-dom@18",
            "node - <<'JS'",
            "const fs = require('node:fs');",
            "const path = require('node:path');",
            "const arch = process.arch;",
            "const expectedPackage = arch === 'arm64'",
            "  ? '@remotion/compositor-linux-arm64-musl'",
            "  : arch === 'x64'",
            "    ? '@remotion/compositor-linux-x64-musl'",
            "    : null;",
            "if (expectedPackage === null) {",
            "  throw new Error(`Unsupported process.arch: ${arch}`);",
            "}",
            "const compositorPackage = require.resolve(`${expectedPackage}/package.json`);",
            "const compositorExecutable = path.join(path.dirname(compositorPackage), 'remotion');",
            "if (!fs.statSync(compositorExecutable).isFile()) {",
            "  throw new Error(`Missing compositor executable: ${compositorExecutable}`);",
            "}",
            "console.log(`selected-compositor=${expectedPackage}`);",
            "console.log(`selected-compositor-package=${compositorPackage}`);",
            "JS",
            "npx remotion render src/index.ts Probe out/probe.mp4 --browser-executable=\"$CHROMIUM_EXECUTABLE_PATH\" --concurrency=1 --log=error",
            "test ! -e node_modules/.remotion/chrome-headless-shell",
            "test -s out/probe.mp4",
            "ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height -of csv=p=0 out/probe.mp4 | grep -q 'h264,160,120'",
            "printf '%s\\n' video-processing-remotion-probe-ok",
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
