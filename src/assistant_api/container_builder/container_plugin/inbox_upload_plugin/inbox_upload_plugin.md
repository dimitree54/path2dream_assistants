---
tags:
  - implementation
  - plugin
---

Этот сервис является реализацией [[../container_plugin.md|ContainerPluginService]].

Его задача — добавить endpoint для загрузки файлов в контейнер, создать директорию `inbox` внутри примонтированной папки и обеспечить сохранение загруженных файлов в эту директорию.

# Responsibility
Единая ответственность этого сервиса — настроить endpoint для загрузки файлов, который сохраняет файлы в подпапку `inbox` внутри примонтированной директории, и возвращает абсолютный путь к файлу внутри контейнера.

То есть он:
- требует `MountMetadata` из стандартного mount-aware state;
- fail fast, если ни один предыдущий plugin не предоставил mount metadata (не примонтирована никакая папка);
- запускает собственный HTTP-сервер (FastAPI) как container managed process, который обслуживает endpoint загрузки;
- принимает файл через multipart/form-data (поле `file`), сохраняет его в `<container_path>/inbox/<original_filename>`;
- создаёт подпапку `inbox` лениво во время обработки upload-запроса;
- при совпадении имён перезаписывает существующий файл;
- отклоняет опасные имена файлов (path traversal, абсолютные пути);
- после успешной загрузки возвращает абсолютный путь к файлу внутри контейнера;
- не управляет другими endpoint контейнера;
- не выполняет иных операций с файлами, кроме создания подпапки `inbox`;
- не настраивает persistence;
- не имеет ограничений на размер загружаемого файла.

# Interfaces
Публичный сервис этой реализации называется `InboxUploadPluginService`.

```python
from assistant_api.container_builder.container_plugin.inbox_upload_plugin import InboxUploadPluginService

plugin = InboxUploadPluginService(host_port=8090)
```

## Init time
```python
class InboxUploadPluginService:
    def __init__(
        self,
        host_port: int = 8090,
        container_port: int | None = None,
        upload_endpoint_path: str = "/api/inbox/upload",
        wait_for_mount: bool = False,
    ) -> None:
        pass
```

Параметры:
- `host_port` — host/external порт, на который публикуется upload endpoint (обязательный, default 8090);
- `container_port` — container-local порт upload HTTP-сервера (если не задан, равен `host_port`);
- `upload_endpoint_path` — путь endpoint для загрузки внутри контейнера (по умолчанию `/api/inbox/upload`).
- `wait_for_mount` — если `False`, сервис fail fast, когда `MountMetadata.container_path` ещё не является mountpoint; если `True`, сервис ждёт mount бесконечно.

## Runtime
Runtime-интерфейс не добавляет ничего нового, а наследуется от [[../container_plugin.md|ContainerPluginService]].

During `configure_container`, the service must read `MountMetadata` from the standard mount-aware state, fail fast if absent, and add a managed process for the FastAPI upload HTTP server. It must not create `inbox` through a startup task. During `configure_image`, the service installs Python 3 and copies the upload handler script into the container image.

During `post_start`, the service must verify inside the container that the upload endpoint accepts a real multipart upload and stores the probe file in `<container_path>/inbox`.

`MountMetadata.container_path` must be mounted before the upload server starts or probes. If `wait_for_mount=False`, the service must fail fast when `container_path` is not a mountpoint. If `wait_for_mount=True`, the service must log a warning and wait indefinitely, periodically checking until `container_path` becomes a mountpoint, before starting the upload server and before running the upload probe.

Upload endpoint поведение (FastAPI HTTP-сервер внутри контейнера):
- Принимает POST-запрос с файлом в multipart/form-data (поле `file`);
- Создаёт `<container_path>/inbox`, если этой директории ещё нет;
- Сохраняет файл в `<container_path>/inbox/<original_filename>`, где `container_path` взят из `MountMetadata`;
- При совпадении имён перезаписывает существующий файл;
- Отклоняет имена файлов, содержащие path traversal (`../`, `..\\`) или являющиеся абсолютными путями — возвращает HTTP 400;
- При успехе возвращает HTTP 200 с JSON: `{"path": "<absolute_container_path>"}`;
- При отсутствии файла в запросе возвращает HTTP 400;
- При GET-запросе возвращает HTTP 405 Method Not Allowed.

# Requirements
- Сервис должен требовать `MountMetadata` из стандартного mount-aware state (`MOUNT_METADATA_STATE_KEY`).
- Сервис должен fail fast, если mount metadata отсутствует — не примонтирована никакая папка.
- Сервис не должен создавать подпапку `inbox` через container startup task.
- Подпапка `inbox` должна создаваться лениво upload handler во время обработки upload-запроса.
- `wait_for_mount` должен default to `False`.
- If `wait_for_mount=False`, the service must fail fast when `MountMetadata.container_path` is not mounted.
- If `wait_for_mount=True`, the service must wait indefinitely for `MountMetadata.container_path` to become mounted.
- During `post_start`, the service must fail fast if the upload endpoint or configured container path is not healthy inside the container.
- Upload endpoint должен работать как container managed process (FastAPI HTTP-сервер), а не через OpenCode Server API.
- `host_port` должен конфигурироваться через init-time параметр и публиковать container port на host.
- `container_port` должен быть конфигурируемым; если не задан — равен `host_port`.
- Endpoint загрузки должен принимать файлы через multipart/form-data с полем `file`.
- Endpoint загрузки должен сохранять файлы с их оригинальными именами внутрь `inbox`, перезаписывая при совпадении.
- Endpoint должен возвращать абсолютный путь к файлу внутри контейнера после успешной загрузки.
- Endpoint должен отклонять имена файлов с path traversal (`../`) или абсолютные пути (`/etc/...`) — HTTP 400.
- Сервис не должен иметь ограничений на размер загружаемого файла.
- Сервис должен работать с любым healthy источником mount (local, Google Drive, и др.).
- Upload handler должен быть написан на Python с использованием FastAPI.

## Sub-services
Не выделяются.
