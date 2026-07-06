---
tags:
  - implementation
  - plugin
---

Этот сервис является реализацией [[../container_plugin.md|ContainerPluginService]].

Его задача — сделать container полностью пригодным для работы с видео end to end: video/audio tooling, полный набор image tooling и web-based renderer support (Node.js + headless Chromium для Remotion-style renderers). Wrapper application подключает только этот один media plugin и не должен дополнительно подключать отдельный image processing plugin.

# Responsibility
Единая ответственность этого сервиса — подготовить image-level dependencies и минимальные Docker runtime settings для видео-работы (включая обработку изображений и web-based rendering) без запуска runtime-процессов.

То есть он:
- устанавливает `ffmpeg` (включая `ffprobe`) для cutting, frame extraction, resize, conversion и audio work;
- устанавливает полный набор image processing tooling как superset: ImageMagick, WebP CLI utilities, HEIC/HEIF CLI utilities, JPEG и PNG optimizers, `file`, system Python с `pillow` и `pillow-heif`;
- устанавливает Node.js с `npm`/`npx` для запуска web-based renderers;
- устанавливает headless Chromium с его runtime libs и базовым набором ttf fonts (включая emoji font) для on-video text rendering;
- задаёт documented env var `CHROMIUM_EXECUTABLE_PATH` с путём к system Chromium executable, чтобы renderers использовали system browser вместо скачивания Chrome Headless Shell at render time;
- запрашивает увеличенный `/dev/shm` для стабильного headless Chromium rendering;
- проверяет после запуска container, что заявленные CLI tools, Chromium executable, fonts и Python modules доступны;
- проверяет после запуска container, что Chromium реально запускается headlessly without network access;
- не запускает managed processes;
- не регистрирует startup tasks;
- не публикует ports;
- не монтирует директории;
- не выполняет обработку видео или изображений сам.

Hardware acceleration (GPU) не входит в контракт этого сервиса: rendering и encoding выполняются на CPU (software rasterizer в headless Chromium).

# Interfaces
Публичный сервис этой реализации называется `VideoProcessingPluginService`.

```python
from assistant_api.container_builder.container_plugin.video_processing_plugin import (
    VideoProcessingPluginService,
)

plugin = VideoProcessingPluginService()
```

## Init time
```python
class VideoProcessingPluginService:
    def __init__(self) -> None:
        pass
```

## Runtime
Runtime-интерфейс не добавляет ничего нового, а наследуется от [[../container_plugin.md|ContainerPluginService]].

During `configure_image`, the service must declare all required system packages through `ImageSpec.apk_packages` and all Python packages through `ImageSpec.python_packages`.

The current container image dependency mechanism uses Alpine packages rendered as `apk add --no-cache`.

System package requirements:
- `ffmpeg` — provides `ffmpeg` and `ffprobe` for cutting, frame extraction, resize, conversion, audio work, and animated formats;
- `imagemagick` — provides ImageMagick commands including `magick` and `identify`;
- `libwebp-tools` — provides WebP utilities including `cwebp`, `dwebp`, and `gif2webp`;
- `libheif-tools` — provides HEIC/HEIF utilities including `heif-convert` and `heif-info`;
- `jpegoptim` — provides additional JPEG optimization;
- `optipng` — provides PNG optimization;
- `pngquant` — provides lossy PNG optimization;
- `file` — provides file type detection by content, not just extension;
- `python3` — provides system Python runtime;
- `py3-pip` — provides system pip installation support;
- `nodejs` — provides Node.js runtime (`node`);
- `npm` — provides `npm` and `npx`;
- `chromium` — provides headless Chromium browser;
- `nss`, `freetype`, `harfbuzz`, `ca-certificates` — provide Chromium runtime libs;
- `fontconfig` — provides font discovery (`fc-list`) for on-video text rendering;
- `ttf-freefont` — provides a base ttf font set;
- `font-noto-emoji` — provides an emoji font (Alpine registers the family as `Noto Color Emoji`);
- `util-linux` — provides `setpriv`, required by Remotion/Chromium browser launch on Alpine.

Python package requirements:
- `pillow` — provides Python image read/resize/save support for JPEG, PNG, WebP, TIFF, and related formats;
- `pillow-heif` — provides Python HEIC/HEIF support for phone photos.

Environment variable contract:
- during `configure_image`, the service must set `ImageSpec.env["CHROMIUM_EXECUTABLE_PATH"]` to the absolute path of the system Chromium executable (`/usr/bin/chromium-browser`);
- the variable is baked into the image, so renderers inside the container (for example Remotion via `--browser-executable="$CHROMIUM_EXECUTABLE_PATH"`) use the system browser instead of downloading Chrome Headless Shell at render time.

During `configure_container`, the service must set `ContainerSpec.shm_size` to `1g`.

During `post_start`, the service must verify inside the running container that the required CLI commands, the Chromium executable, fonts, Python modules, and no-network headless Chromium execution are available.

Required CLI commands:
- `ffmpeg`;
- `ffprobe`;
- `node`;
- `npm`;
- `npx`;
- `magick`;
- `identify`;
- `cwebp`;
- `dwebp`;
- `gif2webp`;
- `heif-convert`;
- `heif-info`;
- `jpegoptim`;
- `optipng`;
- `pngquant`;
- `file`;
- `setpriv`.

Required Chromium checks:
- executable at `$CHROMIUM_EXECUTABLE_PATH` exists and is executable;
- `fc-list` reports at least one installed font.
- `$CHROMIUM_EXECUTABLE_PATH --headless --no-sandbox --disable-gpu --dump-dom 'data:text/html,<html>ok</html>'` must produce the expected DOM text without downloading a browser.

Required Python modules:
- `PIL`;
- `pillow_heif`.

# Requirements
- Сервис должен устанавливать все image dependencies через standard image dependency fields, not raw package-manager install commands.
- Сервис должен использовать `ImageSpec.apk_packages` для системных packages.
- Сервис должен использовать `ImageSpec.python_packages` для Python packages.
- Сервис должен устанавливать `ffmpeg` и обеспечивать доступность commands `ffmpeg` and `ffprobe`.
- Сервис должен предоставлять полный superset image processing tooling: ImageMagick (`magick`, `identify`), WebP tools (`cwebp`, `dwebp`, `gif2webp`), HEIC/HEIF tools (`heif-convert`, `heif-info`), `jpegoptim`, `optipng`, `pngquant`, `file`, system `python3`/`py3-pip` c `pillow` и `pillow-heif`.
- Сервис должен устанавливать Node.js и обеспечивать доступность commands `node`, `npm`, and `npx`.
- Сервис должен устанавливать headless Chromium с runtime libs (`nss`, `freetype`, `harfbuzz`, `ca-certificates`).
- Сервис должен устанавливать базовый ttf font set и emoji font, доступные через `fontconfig`.
- Сервис должен устанавливать `util-linux` и обеспечивать доступность command `setpriv` для Remotion/Chromium launch на Alpine.
- Сервис должен задавать `CHROMIUM_EXECUTABLE_PATH` в `ImageSpec.env` со значением абсолютного пути system Chromium executable.
- Сервис должен задавать `ContainerSpec.shm_size="1g"` для увеличенного `/dev/shm`.
- Container должен позволять real web-based render (Remotion) через system Chromium по пути из `CHROMIUM_EXECUTABLE_PATH`, без скачивания Chrome Headless Shell.
- Сервис не должен добавлять startup tasks.
- Сервис не должен добавлять managed processes.
- Сервис не должен изменять `ContainerSpec.command`.
- Сервис не должен изменять `ContainerSpec.ports`, `ContainerSpec.volumes`, `ContainerSpec.env`, or `ContainerSpec.state`.
- Сервис не должен требовать mount metadata or OpenCode runtime metadata.
- Сервис должен fail fast during `post_start` if any required CLI command is missing.
- Сервис должен fail fast during `post_start` if the Chromium executable at `$CHROMIUM_EXECUTABLE_PATH` is missing or not executable.
- Сервис должен fail fast during `post_start` if no fonts are visible through `fc-list`.
- Сервис должен fail fast during `post_start` if Chromium cannot launch headlessly against a no-network data URL.
- Сервис должен fail fast during `post_start` if `PIL` or `pillow_heif` cannot be imported by `python3`.
- Сервис не должен включать GPU/hardware acceleration в контракт: rendering выполняется на CPU.
- Сервис не должен выполнять video/image conversion, rendering, upload, download, or persistence behavior сам.

## Sub-services
Не выделяются.
