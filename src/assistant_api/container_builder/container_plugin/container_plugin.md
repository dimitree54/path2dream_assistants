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
- Docker runtime capabilities — минимальные Docker options, нужные plugin для запуска container, включая devices, `cap_add`, security options, `mem_limit`, `shm_size` и `restart_policy`.

Стандартное правило image dependencies:
- plugin должен объявлять системные пакеты через `ImageSpec.apk_packages`;
- plugin должен объявлять Python packages для `pip` через `ImageSpec.python_packages`;
- plugin не должен добавлять raw package-manager install commands в `ImageSpec.run_commands`;
- `ContainerBuilderService` отвечает за rendering dependency install commands до plugin runtime commands;
- несколько plugins могут объявить одинаковые package dependencies, а Dockerfile rendering должен de-duplicate их в одном install step каждого типа.

Startup task ownership:
- plugin, который добавляет startup task, считается владельцем этой task;
- `ContainerBuilderService` должен дождаться завершения startup tasks перед `post_start`;
- если startup task завершается ошибкой или не завершается за bounded timeout, startup должен fail fast с именем plugin-владельца и task.

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
- plugin, который публикует port наружу container, может принимать optional init-time `host` bind address;
- если `host` не задан, Docker port publishing должен сохранять default bind behavior;
- если `host` задан, Docker должен bind published port только на этот host address;
- `host` должен быть IP address literal, например `127.0.0.1`; invalid host bind values должны fail fast;
- global builder-level default bind address не входит в standard published ports contract;
- host/external port не должен требоваться через environment variables и не должен вычисляться из state другого plugin;
- container/internal port может быть выбран самим plugin, передан через init-time configuration или взят из environment variable, если это явно задокументировано конкретным plugin.

Стандартное правило fail-fast hooks:
- каждый lifecycle hook отвечает не только за запуск своей логики, но и за проверку, что запущенная plugin feature полностью доступна и здорова внутри container;
- hook не должен успешно возвращаться, пока соответствующая plugin feature не прошла health validation;
- если feature не достигла healthy state за bounded timeout или health validation завершилась ошибкой, hook должен fail fast;
- запуск mount, startup task, managed process, endpoint или другого container-side behavior без проверки его фактической готовности запрещён.

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
- local Docker live container tests являются частью default pytest quality gate;
- `manual` marker разрешён только для tests, которым требуются внешние credentials, human OAuth/login interaction или другой неавтоматизируемый внешний state;
- если live tests помечены как `manual` или исключены из default pytest run, они не считаются частью default quality gate; перед утверждением container-runtime behavior эти tests должны быть запущены явно;
- для каждого manual live test должен существовать non-manual contract test, который ловит наиболее вероятные spec-level причины невозможности запуска в container;
- plugin считается проверенным для container-runtime behavior только после успешного прохождения соответствующих live container integration tests.

# Requirements
- Lifecycle должен быть одинаковым для всех plugin-сервисов.
- Plugin methods должны мутировать переданную spec/context in place.
- Plugin не должен сам запускать container.
- Plugin не должен знать, кто его использует.
- Plugin должен fail fast при невалидной конфигурации или отсутствующих required state.
- Plugin hook должен fail fast, если запущенная им plugin feature не стала полностью healthy внутри container.
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
