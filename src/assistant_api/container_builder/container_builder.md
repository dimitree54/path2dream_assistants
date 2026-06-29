---
tags:
  - entrypoint
---

Этот сервис является основным публичным entrypoint для сборки и запуска OpenCode-oriented контейнеров через Docker SDK for Python.

# Responsibility
Единая ответственность этого сервиса — подготовить Docker image, запустить Docker container и применить подключённые plugin-сервисы.

То есть он:
- применяет plugin lifecycle в заданном пользователем порядке;
- логирует выполнение plugin lifecycle так, чтобы было видно, какой plugin и какой lifecycle stage запускается сейчас;
- строит Docker image динамически или переиспользует уже существующий image в зависимости от build policy;
- renders structured image dependency declarations before plugin runtime commands;
- запускает container через Docker SDK;
- подготавливает startup tasks и long-running processes, если их зарегистрировали plugins;
- применяет Docker runtime capabilities, запрошенные plugins;
- запускает post-start hooks подключённых plugins;
- скрывает детали Dockerfile rendering, Docker SDK calls и container replacement.

При инициализации сервис принимает plugin-сервисы, имя container, image tag и build policy. Все остальные решения и технические параметры сборки и запуска создаются внутри сервиса.

Сервис сам не решает, что монтировать, запускать ли OpenCode Web, включать ли persistence или как называть mounted directory. За это отвечают сервисы, реализующие [[container_plugin/container_plugin.md|ContainerPluginService]] и подающиеся в ContainerBuilderService при инициализации.

# Interfaces
Публичный сервис этого модуля называется `ContainerBuilderService`.

```python
from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin import ContainerPluginService

plugins: list[ContainerPluginService] = [...]
builder = ContainerBuilderService(plugins=plugins, container_name="notes-assistant-opencode")

container = builder.build_and_run()
```

## Init time
```python
class ContainerBuilderService:
    def __init__(
        self,
        plugins: list[ContainerPluginService],
        container_name: str = "notes-assistant-opencode",
        *,
        image_tag: str = "notes-assistant-opencode:latest",
        build_policy: Literal["always", "if_missing", "never"] = "always",
    ) -> None:
        pass
```

## Runtime
```python
class ContainerBuilderService:
    def build(self) -> None:
        pass

    def build_and_run(self) -> RunningContainer:
        pass

    def stop(self, remove: bool = False) -> None:
        pass
```

Используемые интерфейсы:
- [[container_plugin/container_plugin.md|ContainerPluginService]]
- [[running_container_command_runner.md|RunningContainerCommandRunnerService]]

# Requirements
- Minimal builder with an empty plugins list must be valid and must start a long-running inert container.
- `container_name` must define the Docker container name used for start, replacement and stop operations.
- `image_tag` must define the Docker image tag used in `ContainerSpec`, Docker build calls and Docker run calls.
- `build_policy="always"` must preserve default behavior and build the Docker image before starting the container.
- `build_policy="if_missing"` must reuse an existing local image with the configured `image_tag` and build it only when it is missing.
- `build_policy="never"` must never build the Docker image. It must reuse an existing local image with the configured `image_tag`, and must fail fast with an explicit error when the image is missing.
- Image policy decisions must be logged so callers can see when an image was built, reused or rejected.
- Plugins must be applied in caller-provided order.
- Plugin lifecycle execution must be logged with the current plugin name and lifecycle stage before each plugin hook starts.
- Invalid plugin combinations must fail fast with an explicit error.
- `build_and_run()` must resolve the image according to `build_policy` before starting the container.
- Structured `ImageSpec.apk_packages` and `ImageSpec.python_packages` dependencies must be rendered as de-duplicated Dockerfile install steps before raw `ImageSpec.run_commands`.
- Startup tasks registered by plugins must run before managed long-running processes.
- Managed long-running processes registered by plugins must be started by the container entrypoint.
- A raw `ContainerSpec.command` and managed long-running processes must not conflict silently.
- Docker runtime capabilities requested by plugins, including devices, `cap_add`, security options, `mem_limit`, and `restart_policy`, must be passed to Docker SDK when the container starts.
- Post-start hooks must run after Docker reports the container as started.
- `build_and_run()` must validate that all plugin hooks finished successfully before returning `RunningContainer`.
- Any plugin hook failure must fail fast with an explicit error and must prevent `build_and_run()` from returning a successful result.
- An existing container with the configured `container_name` may be force removed before starting the new one, and this replacement must be logged with the container name.

## Sub-services
[[container_plugin/container_plugin.md|ContainerPluginService]]
[[running_container_command_runner.md|RunningContainerCommandRunnerService]]
