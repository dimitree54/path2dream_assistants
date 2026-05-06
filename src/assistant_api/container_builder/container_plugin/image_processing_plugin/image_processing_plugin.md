---
tags:
  - implementation
  - plugin
---

Этот сервис является реализацией [[../container_plugin.md|ContainerPluginService]].

Его задача — добавить в container image системные CLI-инструменты и Python packages для обработки изображений и телефонных фото.

# Responsibility
Единая ответственность этого сервиса — подготовить image-level dependencies для обработки изображений без запуска runtime-процессов и без настройки container behavior.

То есть он:
- устанавливает ImageMagick CLI для базовой работы с изображениями;
- устанавливает `ffmpeg` как универсальный запасной инструмент для resize/conversion и работы с видео/анимированными форматами;
- устанавливает WebP CLI utilities;
- устанавливает HEIC/HEIF CLI utilities для фото с iPhone;
- устанавливает JPEG и PNG optimizers;
- устанавливает Python packages `pillow` и `pillow-heif` в system Python;
- проверяет после запуска container, что заявленные CLI tools и Python modules доступны;
- не запускает managed processes;
- не регистрирует startup tasks;
- не публикует ports;
- не монтирует директории;
- не выполняет обработку изображений сам.

# Interfaces
Публичный сервис этой реализации называется `ImageProcessingPluginService`.

```python
from assistant_api.container_builder.container_plugin.image_processing_plugin import (
    ImageProcessingPluginService,
)

plugin = ImageProcessingPluginService()
```

## Init time
```python
class ImageProcessingPluginService:
    def __init__(self) -> None:
        pass
```

## Runtime
Runtime-интерфейс не добавляет ничего нового, а наследуется от [[../container_plugin.md|ContainerPluginService]].

During `configure_image`, the service must declare all required system packages through `ImageSpec.apk_packages` and all Python packages through `ImageSpec.python_packages`.

The current container image dependency mechanism uses Alpine packages rendered as `apk add --no-cache`. The Debian/Ubuntu package set requested for this capability is represented by equivalent Alpine package names where package names differ.

System package requirements:
- `imagemagick` — provides ImageMagick commands including `magick` and `identify`;
- `ffmpeg` — provides video, animated format, frame extraction, resize, and conversion tooling;
- `libwebp-tools` — provides WebP utilities including `cwebp`, `dwebp`, and `gif2webp`;
- `libheif-tools` — provides HEIC/HEIF utilities including `heif-convert` and `heif-info`;
- `jpegoptim` — provides additional JPEG optimization;
- `optipng` — provides PNG optimization;
- `pngquant` — provides lossy PNG optimization;
- `python3` — provides system Python runtime;
- `py3-pip` — provides system pip installation support.

Python package requirements:
- `pillow` — provides Python image read/resize/save support for JPEG, PNG, WebP, TIFF, and related formats;
- `pillow-heif` — provides Python HEIC/HEIF support for phone photos.

During `configure_container`, the service must not mutate container runtime behavior.

During `post_start`, the service must verify inside the running container that the required CLI commands and Python modules are available.

Required CLI commands:
- `magick`;
- `identify`;
- `ffmpeg`;
- `cwebp`;
- `dwebp`;
- `gif2webp`;
- `heif-convert`;
- `heif-info`;
- `jpegoptim`;
- `optipng`;
- `pngquant`.

Required Python modules:
- `PIL`;
- `pillow_heif`.

# Requirements
- Сервис должен устанавливать все image dependencies через standard image dependency fields, not raw package-manager install commands.
- Сервис должен использовать `ImageSpec.apk_packages` для системных packages.
- Сервис должен использовать `ImageSpec.python_packages` для Python packages.
- Сервис должен устанавливать ImageMagick.
- Сервис должен обеспечивать доступность commands `magick` and `identify`.
- Сервис должен устанавливать `ffmpeg`.
- Сервис должен устанавливать WebP CLI tools.
- Сервис должен обеспечивать доступность commands `cwebp`, `dwebp`, and `gif2webp`.
- Сервис должен устанавливать HEIC/HEIF CLI tools for phone photos.
- Сервис должен обеспечивать доступность commands `heif-convert` and `heif-info`.
- Сервис должен устанавливать `jpegoptim`.
- Сервис должен устанавливать `optipng`.
- Сервис должен устанавливать `pngquant`.
- Сервис должен устанавливать `pillow` into system Python.
- Сервис должен устанавливать `pillow-heif` into system Python.
- Сервис должен устанавливать `python3` and `py3-pip`, because Python image packages are installed through system Python/pip.
- Сервис не должен добавлять startup tasks.
- Сервис не должен добавлять managed processes.
- Сервис не должен изменять `ContainerSpec.command`.
- Сервис не должен изменять `ContainerSpec.ports`, `ContainerSpec.volumes`, `ContainerSpec.env`, or `ContainerSpec.state`.
- Сервис не должен требовать mount metadata or OpenCode runtime metadata.
- Сервис должен fail fast during `post_start` if any required CLI command is missing.
- Сервис должен fail fast during `post_start` if `PIL` or `pillow_heif` cannot be imported by `python3`.
- Сервис не должен выполнять image conversion, resize, optimization, upload, download, or persistence behavior.

## Sub-services
Не выделяются.
