from __future__ import annotations

import inspect

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin.image_processing_plugin import (
    ImageProcessingPluginService,
)
from assistant_api.models import ContainerRuntimeContext


def test_public_service_import_and_init_signature() -> None:
    signature = inspect.signature(ImageProcessingPluginService)

    assert list(signature.parameters) == []


def test_configure_image_declares_image_processing_dependencies() -> None:
    image_spec, container_spec = ContainerBuilderService(
        plugins=[ImageProcessingPluginService()]
    )._prepare_specs()

    assert image_spec.apk_packages == [
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
    assert image_spec.python_packages == ["pillow", "pillow-heif"]
    assert image_spec.env == {}
    assert image_spec.workdir is None
    assert image_spec.command is None
    assert image_spec.run_commands == ["mkdir -p /workspace"]

    assert container_spec.env == {}
    assert container_spec.volumes == {}
    assert container_spec.ports == {}
    assert container_spec.command is None
    assert container_spec.startup_tasks == []
    assert container_spec.managed_processes == []
    assert container_spec.state == {}


def test_post_start_checks_required_cli_tools_and_python_modules() -> None:
    plugin = ImageProcessingPluginService()
    container = _RecordingContainer(exit_code=0)

    plugin.post_start(
        ContainerRuntimeContext(docker_client=object(), container=container, state={})
    )

    assert container.commands
    command_text = container.commands[0][2]
    for command in [
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
    ]:
        assert f"command -v {command}" in command_text
    assert "import PIL" in command_text
    assert "import pillow_heif" in command_text


def test_post_start_fails_when_dependency_health_check_fails() -> None:
    plugin = ImageProcessingPluginService()

    with pytest.raises(RuntimeError, match="image processing health check failed"):
        plugin.post_start(
            ContainerRuntimeContext(
                docker_client=object(),
                container=_RecordingContainer(exit_code=1, output="missing magick"),
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
