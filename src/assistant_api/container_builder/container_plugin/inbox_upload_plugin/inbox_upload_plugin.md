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
- создаёт подпапку `inbox` внутри примонтированной директории (`MountMetadata.container_path`);
- добавляет endpoint для загрузки файлов к существующим endpoint контейнера;
- принимает файл через этот endpoint, сохраняет его в `<container_path>/inbox/<original_filename>`;
- после успешной загрузки возвращает абсолютный путь к файлу внутри контейнера;
- не управляет другими endpoint контейнера;
- не выполняет иных операций с файлами;
- не настраивает persistence.

# Interfaces
Публичный сервис этой реализации называется `InboxUploadPluginService`.

```python
from assistant_api.container_builder.container_plugin.inbox_upload_plugin import InboxUploadPluginService

plugin = InboxUploadPluginService()
```

## Init time
```python
class InboxUploadPluginService:
    def __init__(self, upload_endpoint_path: str = "/api/inbox/upload") -> None:
        pass
```

## Runtime
Runtime-интерфейс не добавляет ничего нового, а наследуется от [[../container_plugin.md|ContainerPluginService]].

During `configure_container`, the service must read `MountMetadata` from the standard mount-aware state, fail fast if absent, and ensure the `inbox` subdirectory is created inside the mounted folder. During `post_start`, the service must register the upload endpoint with the container's existing API service.

Upload endpoint поведение:
- Принимает POST-запрос с файлом в теле запроса;
- Сохраняет файл в `<container_path>/inbox/<original_filename>`, где `container_path` взят из `MountMetadata`;
- При успехе возвращает HTTP 200 с JSON: `{"path": "<absolute_container_path>"}`;
- При ошибке возвращает соответствующий HTTP-код ошибки.

# Requirements
- Сервис должен требовать `MountMetadata` из стандартного mount-aware state (`MOUNT_METADATA_STATE_KEY`).
- Сервис должен fail fast, если mount metadata отсутствует — не примонтирована никакая папка.
- Подпапка `inbox` должна создаваться внутри примонтированной директории, путь к которой указан в `MountMetadata.container_path`.
- Endpoint загрузки должен сохранять файлы с их оригинальными именами внутрь `inbox`.
- Endpoint должен возвращать абсолютный путь к файлу внутри контейнера после успешной загрузки.
- Сервис должен использовать существующий API сервис контейнера для добавления endpoint (например, OpenCode Server API).
- Сервис должен работать с любым источником mount (local, Google Drive, и др.) — `MountMetadata.source_type` не влияет на поведение.

## Sub-services
Не выделяются.
