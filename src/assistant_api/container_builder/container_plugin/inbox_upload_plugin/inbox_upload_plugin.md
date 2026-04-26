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
- создаёт подпапку `inbox` внутри примонтированной директории (`MountMetadata.container_path`) через container startup task;
- запускает собственный HTTP-сервер (FastAPI) как container managed process, который обслуживает endpoint загрузки;
- принимает файл через multipart/form-data (поле `file`), сохраняет его в `<container_path>/inbox/<original_filename>`;
- при совпадении имён перезаписывает существующий файл;
- отклоняет опасные имена файлов (path traversal, абсолютные пути);
- после успешной загрузки возвращает абсолютный путь к файлу внутри контейнера;
- не управляет другими endpoint контейнера;
- не выполняет иных операций с файлами;
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
    ) -> None:
        pass
```

Параметры:
- `host_port` — host/external порт, на который публикуется upload endpoint (обязательный, default 8090);
- `container_port` — container-local порт upload HTTP-сервера (если не задан, равен `host_port`);
- `upload_endpoint_path` — путь endpoint для загрузки внутри контейнера (по умолчанию `/api/inbox/upload`).

## Runtime
Runtime-интерфейс не добавляет ничего нового, а наследуется от [[../container_plugin.md|ContainerPluginService]].

During `configure_container`, the service must read `MountMetadata` from the standard mount-aware state, fail fast if absent, add a startup task to create the `inbox` subdirectory, and add a managed process for the FastAPI upload HTTP server. During `configure_image`, the service installs Python 3 and copies the upload handler script into the container image.

During `post_start`, the service performs no host-side actions — the upload endpoint is served entirely by the container managed process.

Upload endpoint поведение (FastAPI HTTP-сервер внутри контейнера):
- Принимает POST-запрос с файлом в multipart/form-data (поле `file`);
- Сохраняет файл в `<container_path>/inbox/<original_filename>`, где `container_path` взят из `MountMetadata`;
- При совпадении имён перезаписывает существующий файл;
- Отклоняет имена файлов, содержащие path traversal (`../`, `..\\`) или являющиеся абсолютными путями — возвращает HTTP 400;
- При успехе возвращает HTTP 200 с JSON: `{"path": "<absolute_container_path>"}`;
- При отсутствии файла в запросе возвращает HTTP 400;
- При GET-запросе возвращает HTTP 405 Method Not Allowed.

# Requirements
- Сервис должен требовать `MountMetadata` из стандартного mount-aware state (`MOUNT_METADATA_STATE_KEY`).
- Сервис должен fail fast, если mount metadata отсутствует — не примонтирована никакая папка.
- Подпапка `inbox` должна создаваться через container startup task: `mkdir -p <container_path>/inbox`.
- Upload endpoint должен работать как container managed process (FastAPI HTTP-сервер), а не через OpenCode Server API.
- `host_port` должен конфигурироваться через init-time параметр и публиковать container port на host.
- `container_port` должен быть конфигурируемым; если не задан — равен `host_port`.
- Endpoint загрузки должен принимать файлы через multipart/form-data с полем `file`.
- Endpoint загрузки должен сохранять файлы с их оригинальными именами внутрь `inbox`, перезаписывая при совпадении.
- Endpoint должен возвращать абсолютный путь к файлу внутри контейнера после успешной загрузки.
- Endpoint должен отклонять имена файлов с path traversal (`../`) или абсолютные пути (`/etc/...`) — HTTP 400.
- Сервис не должен иметь ограничений на размер загружаемого файла.
- Сервис должен работать с любым источником mount (local, Google Drive, и др.) — `MountMetadata.source_type` не влияет на поведение.
- Upload handler должен быть написан на Python с использованием FastAPI.

## Sub-services
Не выделяются.
