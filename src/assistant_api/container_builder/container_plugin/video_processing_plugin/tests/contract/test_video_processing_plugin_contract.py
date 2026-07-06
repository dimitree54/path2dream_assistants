from __future__ import annotations

import inspect

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin.video_processing_plugin import (
    VideoProcessingPluginService,
)
from assistant_api.models import ContainerRuntimeContext


REQUIRED_CLI_COMMANDS = [
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


def test_public_service_import_and_init_signature() -> None:
    signature = inspect.signature(VideoProcessingPluginService)

    assert list(signature.parameters) == []


def test_configure_image_declares_video_processing_dependencies() -> None:
    image_spec, container_spec = ContainerBuilderService(
        plugins=[VideoProcessingPluginService()]
    )._prepare_specs()

    assert image_spec.apk_packages == [
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
    ]
    assert image_spec.python_packages == ["pillow", "pillow-heif"]
    assert image_spec.env == {"CHROMIUM_EXECUTABLE_PATH": "/usr/bin/chromium-browser"}
    assert image_spec.workdir is None
    assert image_spec.command is None
    assert image_spec.run_commands == ["mkdir -p /workspace"]

    assert container_spec.env == {}
    assert container_spec.volumes == {}
    assert container_spec.ports == {}
    assert container_spec.command is None
    assert container_spec.shm_size == "1g"
    assert container_spec.startup_tasks == []
    assert container_spec.managed_processes == []
    assert container_spec.state == {}


def test_post_start_checks_required_cli_tools_chromium_fonts_and_python_modules() -> None:
    plugin = VideoProcessingPluginService()
    container = _RecordingContainer(exit_code=0)

    plugin.post_start(
        ContainerRuntimeContext(docker_client=object(), container=container, state={})
    )

    assert container.commands
    command_text = container.commands[0][2]
    for command in REQUIRED_CLI_COMMANDS:
        assert f"command -v {command}" in command_text
    assert 'test -x "$CHROMIUM_EXECUTABLE_PATH"' in command_text
    assert "--dump-dom" in command_text
    assert "data:text/html,<html>ok</html>" in command_text
    assert "grep -q ok" in command_text
    assert "fc-list" in command_text
    assert "import PIL" in command_text
    assert "import pillow_heif" in command_text


def test_post_start_fails_when_dependency_health_check_fails() -> None:
    plugin = VideoProcessingPluginService()

    with pytest.raises(RuntimeError, match="video processing health check failed"):
        plugin.post_start(
            ContainerRuntimeContext(
                docker_client=object(),
                container=_RecordingContainer(exit_code=1, output="missing ffprobe"),
                state={},
            )
        )


class _RecordingContainer:
    def __init__(self, exit_code: int, output: str = "") -> None:
        self.exit_code = exit_code
        self.output = output
        self.commands: list[list[str]] = []

    def exec_run(self, command: list[str]) -> object:
        self.commands.append(command)
        exit_code = self.exit_code
        output = self.output.encode("utf-8")

        class Result:
            pass

        result = Result()
        result.exit_code = exit_code
        result.output = output
        return result
