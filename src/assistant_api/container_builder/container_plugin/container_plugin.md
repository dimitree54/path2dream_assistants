---
tags:
  - interface
---

Этот сервис задаёт общий интерфейс plugin-сервисов, которые настраивают container builder.

Его задача — дать один lifecycle для независимых расширений container build/run процесса.

# Responsibility
Единая ответственность этого сервиса — определить общий контракт plugin-сервиса для настройки image, container и post-start поведения.

То есть plugin:
- может изменить `ImageSpec` до сборки image;
- может изменить `ContainerSpec` до запуска container;
- может зарегистрировать startup tasks, которые выполняются до long-running processes;
- может зарегистрировать managed long-running processes;
- может запросить Docker runtime capabilities;
- может выполнить post-start действие после запуска container;
- может передать другим plugins общие данные через `ContainerSpec.state`;
- скрывает собственные implementation details от `ContainerBuilderService`.

# Interfaces
Публичный interface-сервис этого модуля называется `ContainerPluginService`.

```python
from assistant_api.container_builder.container_plugin import ContainerPluginService
```

## Init time
Init-time конфигурация зависит от конкретной реализации plugin-сервиса и не входит в общий интерфейс.

## Runtime
```python
class ContainerPluginService:
    name: str

    def configure_image(self, image: ImageSpec) -> None:
        pass

    def configure_container(self, container: ContainerSpec) -> None:
        pass

    def post_start(self, runtime: ContainerRuntimeContext) -> None:
        pass
```

Shared runtime-модели этого интерфейса находятся в `assistant_api.models`.

Стандартные container capabilities:
- startup task — одноразовая команда, которая должна завершиться успешно до запуска long-running processes;
- managed process — long-running process, которым управляет container entrypoint;
- Docker runtime capabilities — минимальные Docker options, нужные plugin для запуска container, включая devices, `cap_add` и security options.

Стандартный mount-aware state:
- `MOUNT_METADATA_STATE_KEY = "mount"`;
- plugin, который предоставляет mount source, должен записывать туда `MountMetadata`;
- plugin, которому нужен mount source, должен читать `MountMetadata` оттуда и fail fast, если metadata нет.

# Requirements
- Lifecycle должен быть одинаковым для всех plugin-сервисов.
- Plugin methods должны мутировать переданную spec/context in place.
- Plugin не должен сам запускать container.
- Plugin не должен знать, кто его использует.
- Plugin должен fail fast при невалидной конфигурации или отсутствующих required state.
- Startup tasks from plugins must run before managed long-running processes.
- Managed processes from several plugins must be composed without overwriting each other.
- Raw `ContainerSpec.command` and managed processes must not be used together silently.
- Docker runtime capabilities requested by plugins must be explicit in `ContainerSpec`.
- Shared state должен использоваться только для маленьких typed coordination models, а не для скрытой передачи implementation details.

## Sub-services
Не выделяются.
