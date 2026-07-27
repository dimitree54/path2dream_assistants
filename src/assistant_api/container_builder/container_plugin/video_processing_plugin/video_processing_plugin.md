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
- устанавливает полный набор image processing tooling как superset: ImageMagick, WebP CLI utilities, HEIC/HEIF CLI utilities, JPEG и PNG optimizers, `file`, system Python с `pillow`, `pillow-heif` и `requests`;
- устанавливает Node.js с `npm`/`npx` для запуска web-based renderers;
- устанавливает headless Chromium с его runtime libs и базовым набором ttf fonts (включая emoji font) для on-video text rendering;
- устанавливает `gcompat`, чтобы published Remotion Linux compositor packages могли использовать соответствующий архитектуре glibc compatibility loader на Alpine;
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
- `util-linux` — provides `setpriv`, required by Remotion/Chromium browser launch on Alpine;
- `gcompat` — provides the architecture-native glibc compatibility loader required by published Remotion Linux compositor executables on Alpine (`/lib/ld-linux-aarch64.so.1` for `arm64`, `/lib64/ld-linux-x86-64.so.2` for `x64`).

Python package requirements:
- `pillow` — provides Python image read/resize/save support for JPEG, PNG, WebP, TIFF, and related formats;
- `pillow-heif` — provides Python HEIC/HEIF support for phone photos;
- `requests` — provides image-level HTTP access for visual tooling such as SAM3 helpers without per-job dependency installation.

Environment variable contract:
- during `configure_image`, the service must set `ImageSpec.env["CHROMIUM_EXECUTABLE_PATH"]` to the absolute path of the system Chromium executable (`/usr/bin/chromium-browser`);
- the variable is baked into the image, so renderers inside the container (for example Remotion via `--browser-executable="$CHROMIUM_EXECUTABLE_PATH"`) use the system browser instead of downloading Chrome Headless Shell at render time.

During `configure_container`, the service must set `ContainerSpec.shm_size` to `1g`.

During `post_start`, the service must verify inside the running container that the required CLI commands, the architecture-native `gcompat` loader, the Chromium executable, fonts, Python modules, and no-network headless Chromium execution are available.

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

Required Remotion compatibility checks:
- `gcompat` must be installed through `ImageSpec.apk_packages`;
- on `aarch64`, `/lib/ld-linux-aarch64.so.1` must exist and be executable;
- on `x86_64`, `/lib64/ld-linux-x86-64.so.2` must exist and be executable;
- other container architectures are unsupported and must fail fast;
- QA must perform a real Remotion render on native `arm64` and native `x64`, verify the compositor package selected for `process.arch`, and probe the resulting non-empty MP4;
- the service must not rewrite compositor executables, create workspace-local loader symlinks, or emulate a different production architecture.

Required Python modules:
- `PIL`;
- `pillow_heif`;
- `requests`.

# Requirements
- Сервис должен устанавливать все image dependencies через standard image dependency fields, not raw package-manager install commands.
- Сервис должен использовать `ImageSpec.apk_packages` для системных packages.
- Сервис должен использовать `ImageSpec.python_packages` для Python packages.
- Сервис должен устанавливать `ffmpeg` и обеспечивать доступность commands `ffmpeg` and `ffprobe`.
- Сервис должен предоставлять полный superset image processing tooling: ImageMagick (`magick`, `identify`), WebP tools (`cwebp`, `dwebp`, `gif2webp`), HEIC/HEIF tools (`heif-convert`, `heif-info`), `jpegoptim`, `optipng`, `pngquant`, `file`, system `python3`/`py3-pip` c `pillow`, `pillow-heif` и `requests`.
- Сервис должен устанавливать Node.js и обеспечивать доступность commands `node`, `npm`, and `npx`.
- Сервис должен устанавливать headless Chromium с runtime libs (`nss`, `freetype`, `harfbuzz`, `ca-certificates`).
- Сервис должен устанавливать базовый ttf font set и emoji font, доступные через `fontconfig`.
- Сервис должен устанавливать `util-linux` и обеспечивать доступность command `setpriv` для Remotion/Chromium launch на Alpine.
- Сервис должен устанавливать `gcompat` через `ImageSpec.apk_packages` и проверять architecture-native compatibility loader для Remotion compositor execution.
- Сервис должен задавать `CHROMIUM_EXECUTABLE_PATH` в `ImageSpec.env` со значением абсолютного пути system Chromium executable.
- Сервис должен задавать `ContainerSpec.shm_size="1g"` для увеличенного `/dev/shm`.
- Container должен позволять real web-based render (Remotion) через system Chromium по пути из `CHROMIUM_EXECUTABLE_PATH`, без скачивания Chrome Headless Shell.
- Container должен использовать published Remotion musl compositor package для native `process.arch`: `@remotion/compositor-linux-arm64-musl` на `arm64` и `@remotion/compositor-linux-x64-musl` на `x64`.
- Сервис не должен patch/rewrite compositor executable, создавать loader symlink внутри user workspace или подменять production architecture через emulation.
- Сервис не должен добавлять startup tasks.
- Сервис не должен добавлять managed processes.
- Сервис не должен изменять `ContainerSpec.command`.
- Сервис не должен изменять `ContainerSpec.ports`, `ContainerSpec.volumes`, `ContainerSpec.env`, or `ContainerSpec.state`.
- Сервис не должен требовать mount metadata or OpenCode runtime metadata.
- Сервис должен fail fast during `post_start` if any required CLI command is missing.
- Сервис должен fail fast during `post_start` if the Chromium executable at `$CHROMIUM_EXECUTABLE_PATH` is missing or not executable.
- Сервис должен fail fast during `post_start` if the architecture-native `gcompat` loader is missing, not executable, or the container architecture is unsupported.
- Сервис должен fail fast during `post_start` if no fonts are visible through `fc-list`.
- Сервис должен fail fast during `post_start` if Chromium cannot launch headlessly against a no-network data URL.
- Сервис должен fail fast during `post_start` if `PIL`, `pillow_heif`, or `requests` cannot be imported by `python3`.
- Image-level visual tooling должно использовать preinstalled `requests`, `PIL` и `pillow_heif` через system `python3` без per-job `pip install`, temporary virtual environment или dependency download.
- Сервис не должен включать GPU/hardware acceleration в контракт: rendering выполняется на CPU.
- Сервис не должен выполнять video/image conversion, rendering, upload, download, or persistence behavior сам.

## Sub-services
Не выделяются.
