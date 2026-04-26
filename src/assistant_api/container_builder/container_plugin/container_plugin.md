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

Стандартное правило host/container responsibility:
- host-side plugin logic should stay minimal and limited to preparing specs, validating configuration, and explicit short post-start checks;
- most runtime behavior must happen inside the container;
- long-running behavior must be modeled as container startup tasks, managed processes, container commands, or container-side services;
- plugins should not rely on host-side background threads, host-side HTTP listeners, or host-side long-running processes to keep container-published functionality available.

Стандартный mount-aware state:
- `MOUNT_METADATA_STATE_KEY = "mount"`;
- plugin, который предоставляет mount source, должен записывать туда `MountMetadata`;
- plugin, которому нужен mount source, должен читать `MountMetadata` оттуда и fail fast, если metadata нет.

Стандартный OpenCode runtime state:
- `OPENCODE_RUNTIME_STATE_KEY = "opencode_runtime"`;
- plugin, который запускает OpenCode, должен записывать туда `OpenCodeRuntimeMetadata`;
- `OpenCodeRuntimeMetadata.working_dir` должен содержать final container directory, из которой запускается OpenCode;
- `OpenCodeRuntimeMetadata.api_container_port` должен содержать container-local TCP port OpenCode server API;
- plugin, которому нужна директория запуска OpenCode или локальный OpenCode API, должен читать эту metadata из state и fail fast, если metadata нет.

Стандартное правило published ports:
- plugin, который публикует user-facing service наружу container, должен принимать host/external port через init-time configuration;
- host/external port не должен требоваться через environment variables и не должен вычисляться из state другого plugin;
- container/internal port может быть выбран самим plugin, передан через init-time configuration или взят из environment variable, если это явно задокументировано конкретным plugin.

Стандартное правило тестирования plugin:
- contract tests должны покрывать каждое публичное требование plugin-документации;
- spec-level tests обязательны для проверки `ImageSpec`, `ContainerSpec`, shared state, startup tasks, managed processes, ports, volumes и Docker runtime capabilities;
- spec-level tests не доказывают, что plugin работает внутри container;
- если plugin меняет `ImageSpec`, contract tests должны проверять, что все команды, используемые plugin runtime, устанавливаются или становятся доступными до первого использования;
- если plugin использует package manager, interpreter, CLI tool или Python module в image/run commands, tests должны проверять prerequisites явно, а не только наличие итоговой команды;
- если plugin добавляет startup task, managed process, published port или container-side endpoint, у него должен быть live container integration test, который собирает image, запускает container с минимальным набором cooperating plugins и проверяет публичное runtime-поведение через внешний контракт;
- live container integration test должен отдельно проверять, что image собирается с plugin dependencies до проверки endpoint/process behavior;
- endpoint/process tests должны выполнять реальные HTTP/CLI/file operations через published contract, а не только проверять command strings;
- tests для post-response или background effects должны ждать documented effect в bounded timeout, а не полагаться на синхронное выполнение в тот же момент получения ответа;
- если live tests помечены как `manual` или исключены из default pytest run, они не считаются частью default quality gate; перед утверждением container-runtime behavior эти tests должны быть запущены явно;
- для каждого manual live test должен существовать non-manual contract test, который ловит наиболее вероятные spec-level причины невозможности запуска в container;
- plugin считается проверенным для container-runtime behavior только после успешного прохождения соответствующих live container integration tests.

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
- Host-side plugin logic must stay minimal; most runtime behavior, especially long-running behavior, must run inside the container.
- Shared state должен использоваться только для маленьких typed coordination models, а не для скрытой передачи implementation details.
- Plugin coordination through `ContainerSpec.state` must replace duplicated caller-provided configuration when one plugin can provide the required runtime fact to another plugin.
- User-facing host ports must be configured through plugin init-time configuration, not through caller-provided environment variables.
- Plugin tests must distinguish spec correctness from real container-runtime correctness.
- Container-runtime behavior must be verified by live container integration tests.

## Sub-services
Не выделяются.
