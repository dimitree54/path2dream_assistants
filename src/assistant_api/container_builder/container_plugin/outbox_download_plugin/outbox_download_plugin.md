---
tags:
  - implementation
  - plugin
---

Этот сервис является реализацией [[../container_plugin.md|ContainerPluginService]].

Его задача — добавить endpoints для листинга и скачивания файлов из контейнера, создать директорию `outbox` внутри примонтированной папки и обеспечить удаление файла после успешного скачивания.

# Responsibility
Единая ответственность этого сервиса — настроить endpoints для листинга файлов в outbox-директории и скачивания отдельных файлов с последующим удалением.

То есть он:
- требует `MountMetadata` из стандартного mount-aware state;
- fail fast, если ни один предыдущий plugin не предоставил mount metadata (не примонтирована никакая папка);
- запускает собственный HTTP-сервер (FastAPI) как container managed process, который обслуживает outbox endpoints;
- не создаёт подпапку `outbox` во время container startup;
- предоставляет endpoint для листинга файлов в `<container_path>/outbox` (GET-запрос);
- предоставляет endpoint для скачивания конкретного файла по имени (GET-запрос); после успешного скачивания файл удаляется из outbox;
- отклоняет опасные имена файлов в download endpoint (path traversal, абсолютные пути);
- не управляет другими endpoint контейнера;
- не выполняет иных операций с файлами;
- не настраивает persistence;
- не имеет ограничений на размер скачиваемого файла.

# Interfaces
Публичный сервис этой реализации называется `OutboxDownloadPluginService`.

```python
from assistant_api.container_builder.container_plugin.outbox_download_plugin import OutboxDownloadPluginService

plugin = OutboxDownloadPluginService(host_port=8090)
```

## Init time
```python
class OutboxDownloadPluginService:
    def __init__(
        self,
        host_port: int = 8090,
        container_port: int | None = None,
        list_endpoint_path: str = "/api/outbox/list",
        download_endpoint_path: str = "/api/outbox/download",
        wait_for_mount: bool = False,
        host: str | None = None,
    ) -> None:
        pass
```

Параметры:
- `host_port` — host/external порт, на который публикуются outbox endpoints (обязательный, default 8090);
- `container_port` — container-local порт outbox HTTP-сервера (если не задан, равен `host_port`);
- `list_endpoint_path` — путь endpoint для листинга файлов внутри контейнера (по умолчанию `/api/outbox/list`);
- `download_endpoint_path` — путь endpoint для скачивания файла внутри контейнера (по умолчанию `/api/outbox/download`).
- `wait_for_mount` — если `False`, сервис fail fast, когда `MountMetadata.container_path` ещё не является mountpoint; если `True`, сервис ждёт mount бесконечно.
- `host` — optional Docker host bind address; если не задан, сохраняется default Docker bind behavior.

## Runtime
Runtime-интерфейс не добавляет ничего нового, а наследуется от [[../container_plugin.md|ContainerPluginService]].

During `configure_container`, the service must read `MountMetadata` from the standard mount-aware state, fail fast if absent, and add a managed process for the FastAPI outbox HTTP server. It must not create `outbox` through a startup task. During `configure_image`, the service installs Python 3 and copies the outbox handler script into the container image.

During `post_start`, the service must verify inside the container that the list endpoint can see a probe file, the download endpoint returns that file, and the file is removed after download.

`MountMetadata.container_path` must be mounted before the outbox server starts or probes. If `wait_for_mount=False`, the service must fail fast when `container_path` is not a mountpoint. If `wait_for_mount=True`, the service must log a warning and wait indefinitely, periodically checking until `container_path` becomes a mountpoint, before starting the outbox server and before running the outbox probe.

### List endpoint
Листинг endpoint поведение (GET-запрос):
- Возвращает HTTP 200 с JSON-массивом, содержащим имена файлов в директории `outbox`;
- Если директория отсутствует или пуста — возвращает HTTP 200 с пустым JSON-массивом `[]`;
- POST, PUT, DELETE и другие методы — возвращает HTTP 405 Method Not Allowed.

### Download endpoint
Скачивание endpoint поведение (GET-запрос):
- Принимает `{filename}` как path-параметр (например, `/api/outbox/download/report.pdf`);
- Если файл с указанным именем существует в `<container_path>/outbox/<filename>` — возвращает HTTP 200 с содержимым файла в теле ответа;
- После успешной отправки содержимого файл удаляется из outbox-директории;
- Если файл не найден — возвращает HTTP 404;
- Если директория `outbox` отсутствует — возвращает HTTP 404;
- Отклоняет имена файлов, содержащие path traversal (`../`, `..\\`) или являющиеся абсолютными путями — возвращает HTTP 400;
- POST, PUT, DELETE и другие методы — возвращает HTTP 405 Method Not Allowed.

# Requirements
- Сервис должен требовать `MountMetadata` из стандартного mount-aware state (`MOUNT_METADATA_STATE_KEY`).
- Сервис должен fail fast, если mount metadata отсутствует — не примонтирована никакая папка.
- Сервис не должен создавать подпапку `outbox` через container startup task.
- `wait_for_mount` должен default to `False`.
- If `wait_for_mount=False`, the service must fail fast when `MountMetadata.container_path` is not mounted.
- If `wait_for_mount=True`, the service must wait indefinitely for `MountMetadata.container_path` to become mounted.
- During `post_start`, the service must fail fast if the outbox endpoints or configured container path are not healthy inside the container.
- Outbox endpoints должны работать как container managed process (FastAPI HTTP-сервер), а не через OpenCode Server API.
- `host_port` должен конфигурироваться через init-time параметр и публиковать container port на host.
- `container_port` должен быть конфигурируемым; если не задан — равен `host_port`.
- `host` должен быть optional init-time параметром для Docker host bind address; если задан, outbox endpoints должны публиковаться только на этот host address.
- Invalid `host` bind values must fail fast.
- List endpoint должен возвращать JSON-массив с именами файлов в директории `outbox` по GET-запросу.
- List endpoint должен возвращать пустой массив `[]`, если директория пуста.
- Download endpoint должен принимать имя файла как path-параметр по GET-запросу.
- Download endpoint должен возвращать содержимое файла в теле ответа (HTTP 200).
- Download endpoint должен удалять файл из outbox-директории после успешной отправки содержимого.
- Download endpoint должен возвращать HTTP 404, если файл не найден.
- Download endpoint должен отклонять имена файлов с path traversal (`../`) или абсолютные пути (`/etc/...`) — HTTP 400.
- Сервис не должен иметь ограничений на размер скачиваемого файла.
- Сервис должен работать с любым healthy источником mount (local, Google Drive, и др.).
- Outbox handler должен быть написан на Python с использованием FastAPI.

## Sub-services
Не выделяются.
