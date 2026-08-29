from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import inspect
import logging
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

import assistant_api.container_builder.container_builder_service as container_builder_service
from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.container_builder.container_plugin.local_dir_mount_plugin import LocalDirMountPluginService
from assistant_api.container_builder.container_plugin.opencode_persistence_plugin import (
    OpenCodePersistencePluginService,
)
from assistant_api.container_builder.container_plugin.opencode_web_server_plugin import (
    OpenCodeWebServerPluginService,
)
from assistant_api.models import (
    ContainerExecutionIdentity,
    ContainerSpec,
    ImageSpec,
    VolumeMount,
)


class _MissingContainers:
    def get(self, _container_name: str) -> Any:
        raise LookupError("container does not exist")


class _DockerClient:
    containers = _MissingContainers()


class _ImageStore:
    def __init__(self, calls: list[object], existing_tags: set[str]) -> None:
        self._calls = calls
        self._existing_tags = existing_tags

    def get(self, image_tag: str) -> object:
        self._calls.append(("image_get", image_tag))
        if image_tag not in self._existing_tags:
            from docker.errors import ImageNotFound

            raise ImageNotFound(f"image does not exist: {image_tag}")
        return object()


class _DockerClientWithImages:
    def __init__(self, calls: list[object], existing_tags: set[str]) -> None:
        self.containers = _MissingContainers()
        self.images = _ImageStore(calls, existing_tags)


class _Container:
    id = "container-id"
    name = "notes-assistant-opencode"


class _ExistingContainer:
    def __init__(self, calls: list[object]) -> None:
        self._calls = calls

    def remove(self, force: bool = False) -> None:
        self._calls.append(("remove", force))


class _ExistingContainers:
    def __init__(self, calls: list[object]) -> None:
        self._calls = calls

    def get(self, container_name: str) -> _ExistingContainer:
        self._calls.append(("get", container_name))
        return _ExistingContainer(self._calls)


class _DockerClientWithExistingContainer:
    def __init__(self, calls: list[object]) -> None:
        self.containers = _ExistingContainers(calls)


def test_init_accepts_plugins_and_container_name() -> None:
    signature = inspect.signature(ContainerBuilderService)

    assert list(signature.parameters) == [
        "plugins",
        "container_name",
        "image_tag",
        "build_policy",
        "execution_identity",
    ]
    assert signature.parameters["plugins"].default is inspect.Parameter.empty
    assert signature.parameters["container_name"].default == "notes-assistant-opencode"
    assert signature.parameters["image_tag"].default == "notes-assistant-opencode:latest"
    assert signature.parameters["image_tag"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["build_policy"].default == "always"
    assert signature.parameters["build_policy"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["execution_identity"].default is None
    assert signature.parameters["execution_identity"].kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.parametrize(
    ("uid", "gid", "umask"),
    [
        (0, 10001, 0o022),
        (10001, 0, 0o022),
        (True, 10001, 0o022),
        (2**32 - 1, 10001, 0o022),
        (10001, 10001, 0o1000),
    ],
)
def test_execution_identity_rejects_root_non_numeric_and_invalid_umask(
    uid: object,
    gid: object,
    umask: object,
) -> None:
    with pytest.raises(ValueError):
        ContainerExecutionIdentity(uid=uid, gid=gid, umask=umask)  # type: ignore[arg-type]


def test_conflicting_plugin_execution_identity_fails_before_docker_work() -> None:
    first = ContainerExecutionIdentity(uid=10001, gid=10001, umask=0o022)
    conflicting = ContainerExecutionIdentity(uid=10002, gid=10002, umask=0o077)

    class _IdentityPlugin:
        name = "identity"

        def configure_image(self, _image: ImageSpec) -> None:
            return None

        def configure_container(self, container: ContainerSpec) -> None:
            container.require_execution_identity(conflicting)

        def post_start(self, _runtime: object) -> None:
            return None

    with pytest.raises(ConfigurationError, match="execution identity"):
        ContainerBuilderService(
            plugins=[_IdentityPlugin()],  # type: ignore[list-item]
            execution_identity=first,
        )._prepare_specs()


def test_execution_identity_rejects_writable_named_volume_before_docker_work() -> None:
    class _NamedVolumePlugin:
        name = "named-volume"

        def configure_image(self, _image: ImageSpec) -> None:
            return None

        def configure_container(self, container: ContainerSpec) -> None:
            container.volumes["root-created"] = VolumeMount(
                source="root-created",
                target=PurePosixPath("/tmp/data"),
                type="volume",
            )

        def post_start(self, _runtime: object) -> None:
            return None

    with pytest.raises(ConfigurationError, match="named volumes"):
        ContainerBuilderService(
            plugins=[_NamedVolumePlugin()],  # type: ignore[list-item]
            execution_identity=ContainerExecutionIdentity(10001, 10001, 0o022),
        )._prepare_specs()


def test_invalid_build_policy_fails_fast() -> None:
    with pytest.raises(ConfigurationError, match="build_policy"):
        ContainerBuilderService(plugins=[], build_policy="sometimes")


def test_run_is_not_public_interface() -> None:
    assert not hasattr(ContainerBuilderService(plugins=[]), "run")


def test_build_builds_image_without_exposing_docker_result(monkeypatch) -> None:
    calls = []

    def build_image(_docker_client: Any, image_spec: ImageSpec, image_tag: str) -> object:
        calls.append((image_spec, image_tag))
        return object()

    monkeypatch.setattr(container_builder_service, "build_image", build_image)

    builder = ContainerBuilderService(plugins=[])
    builder._docker_client = _DockerClient()

    assert builder.build() is None
    assert calls[0][1] == "notes-assistant-opencode:latest"


def test_configured_image_tag_propagates_to_specs_build_and_run(monkeypatch) -> None:
    calls = []

    def build_image(_docker_client: Any, _image_spec: ImageSpec, image_tag: str) -> object:
        calls.append(("build", image_tag))
        return object()

    def ensure_named_volumes(_docker_client: Any, _container_spec: ContainerSpec) -> None:
        calls.append("volumes")

    def run_container(_docker_client: Any, container_spec: ContainerSpec) -> _Container:
        calls.append(("run", container_spec.image_tag))
        return _Container()

    monkeypatch.setattr(container_builder_service, "build_image", build_image)
    monkeypatch.setattr(container_builder_service, "ensure_named_volumes", ensure_named_volumes)
    monkeypatch.setattr(container_builder_service, "run_container", run_container)

    builder = ContainerBuilderService(plugins=[], image_tag="custom-assistant:issue-9")
    builder._docker_client = _DockerClient()

    _image_spec, container_spec = builder._prepare_specs()
    running = builder.build_and_run()

    assert container_spec.image_tag == "custom-assistant:issue-9"
    assert running.container_spec.image_tag == "custom-assistant:issue-9"
    assert calls == [
        ("build", "custom-assistant:issue-9"),
        "volumes",
        ("run", "custom-assistant:issue-9"),
    ]


def test_build_and_run_builds_before_starting_container(monkeypatch) -> None:
    calls = []

    def build_image(_docker_client: Any, _image_spec: ImageSpec, _image_tag: str) -> object:
        calls.append("build")
        return object()

    def ensure_named_volumes(_docker_client: Any, _container_spec: ContainerSpec) -> None:
        calls.append("volumes")

    def run_container(_docker_client: Any, _container_spec: ContainerSpec) -> _Container:
        calls.append("run")
        return _Container()

    monkeypatch.setattr(container_builder_service, "build_image", build_image)
    monkeypatch.setattr(container_builder_service, "ensure_named_volumes", ensure_named_volumes)
    monkeypatch.setattr(container_builder_service, "run_container", run_container)

    builder = ContainerBuilderService(plugins=[])
    builder._docker_client = _DockerClient()

    running = builder.build_and_run()

    assert running.name == "notes-assistant-opencode"
    assert calls == ["build", "volumes", "run"]


def test_build_and_run_holds_image_lock_through_container_start(monkeypatch) -> None:
    calls: list[object] = []

    @contextmanager
    def image_lock(image_tag: str) -> Iterator[None]:
        calls.append(("lock", image_tag))
        yield
        calls.append(("unlock", image_tag))

    def build_image(_docker_client: Any, _image_spec: ImageSpec, image_tag: str) -> object:
        calls.append(("build", image_tag))
        return object()

    def ensure_named_volumes(_docker_client: Any, _container_spec: ContainerSpec) -> None:
        calls.append("volumes")

    def run_container(_docker_client: Any, container_spec: ContainerSpec) -> _Container:
        calls.append(("run", container_spec.image_tag))
        return _Container()

    monkeypatch.setattr(container_builder_service, "image_lock", image_lock)
    monkeypatch.setattr(container_builder_service, "build_image", build_image)
    monkeypatch.setattr(container_builder_service, "ensure_named_volumes", ensure_named_volumes)
    monkeypatch.setattr(container_builder_service, "run_container", run_container)

    builder = ContainerBuilderService(plugins=[], image_tag="shared:latest")
    builder._docker_client = _DockerClient()

    builder.build_and_run()

    assert calls == [
        ("lock", "shared:latest"),
        ("build", "shared:latest"),
        "volumes",
        ("run", "shared:latest"),
        ("unlock", "shared:latest"),
    ]


def test_build_and_run_builds_when_if_missing_image_is_absent(
    monkeypatch,
    caplog,
) -> None:
    calls: list[object] = []

    def build_image(_docker_client: Any, _image_spec: ImageSpec, image_tag: str) -> object:
        calls.append(("build", image_tag))
        return object()

    def ensure_named_volumes(_docker_client: Any, _container_spec: ContainerSpec) -> None:
        calls.append("volumes")

    def run_container(_docker_client: Any, container_spec: ContainerSpec) -> _Container:
        calls.append(("run", container_spec.image_tag))
        return _Container()

    monkeypatch.setattr(container_builder_service, "build_image", build_image)
    monkeypatch.setattr(container_builder_service, "ensure_named_volumes", ensure_named_volumes)
    monkeypatch.setattr(container_builder_service, "run_container", run_container)
    caplog.set_level(logging.INFO, logger=container_builder_service.__name__)

    builder = ContainerBuilderService(
        plugins=[],
        image_tag="missing-assistant:latest",
        build_policy="if_missing",
    )
    builder._docker_client = _DockerClientWithImages(calls, existing_tags=set())

    builder.build_and_run()

    assert calls == [
        ("image_get", "missing-assistant:latest"),
        ("build", "missing-assistant:latest"),
        "volumes",
        ("run", "missing-assistant:latest"),
    ]
    assert "Docker image built: tag=missing-assistant:latest policy=if_missing" in [
        record.getMessage() for record in caplog.records
    ]


def test_build_and_run_reuses_existing_image_with_if_missing(
    monkeypatch,
    caplog,
) -> None:
    calls: list[object] = []

    def build_image(_docker_client: Any, _image_spec: ImageSpec, image_tag: str) -> object:
        calls.append(("build", image_tag))
        return object()

    def ensure_named_volumes(_docker_client: Any, _container_spec: ContainerSpec) -> None:
        calls.append("volumes")

    def run_container(_docker_client: Any, container_spec: ContainerSpec) -> _Container:
        calls.append(("run", container_spec.image_tag))
        return _Container()

    monkeypatch.setattr(container_builder_service, "build_image", build_image)
    monkeypatch.setattr(container_builder_service, "ensure_named_volumes", ensure_named_volumes)
    monkeypatch.setattr(container_builder_service, "run_container", run_container)
    caplog.set_level(logging.INFO, logger=container_builder_service.__name__)

    builder = ContainerBuilderService(
        plugins=[],
        image_tag="cached-assistant:latest",
        build_policy="if_missing",
    )
    builder._docker_client = _DockerClientWithImages(
        calls,
        existing_tags={"cached-assistant:latest"},
    )

    builder.build_and_run()

    assert calls == [
        ("image_get", "cached-assistant:latest"),
        "volumes",
        ("run", "cached-assistant:latest"),
    ]
    assert "Docker image reused: tag=cached-assistant:latest policy=if_missing" in [
        record.getMessage() for record in caplog.records
    ]


def test_build_reuses_existing_image_with_never_policy(monkeypatch, caplog) -> None:
    calls: list[object] = []

    def build_image(_docker_client: Any, _image_spec: ImageSpec, image_tag: str) -> object:
        calls.append(("build", image_tag))
        return object()

    monkeypatch.setattr(container_builder_service, "build_image", build_image)
    caplog.set_level(logging.INFO, logger=container_builder_service.__name__)

    builder = ContainerBuilderService(
        plugins=[],
        image_tag="prebuilt-assistant:latest",
        build_policy="never",
    )
    builder._docker_client = _DockerClientWithImages(
        calls,
        existing_tags={"prebuilt-assistant:latest"},
    )

    builder.build()

    assert calls == [("image_get", "prebuilt-assistant:latest")]
    assert "Docker image reused: tag=prebuilt-assistant:latest policy=never" in [
        record.getMessage() for record in caplog.records
    ]


def test_build_and_run_rejects_missing_image_with_never_policy(
    monkeypatch,
    caplog,
) -> None:
    calls: list[object] = []

    def build_image(_docker_client: Any, _image_spec: ImageSpec, image_tag: str) -> object:
        calls.append(("build", image_tag))
        return object()

    def ensure_named_volumes(_docker_client: Any, _container_spec: ContainerSpec) -> None:
        calls.append("volumes")

    def run_container(_docker_client: Any, container_spec: ContainerSpec) -> _Container:
        calls.append(("run", container_spec.image_tag))
        return _Container()

    monkeypatch.setattr(container_builder_service, "build_image", build_image)
    monkeypatch.setattr(container_builder_service, "ensure_named_volumes", ensure_named_volumes)
    monkeypatch.setattr(container_builder_service, "run_container", run_container)
    caplog.set_level(logging.INFO, logger=container_builder_service.__name__)

    builder = ContainerBuilderService(
        plugins=[],
        image_tag="missing-prebuilt:latest",
        build_policy="never",
    )
    builder._docker_client = _DockerClientWithImages(calls, existing_tags=set())

    with pytest.raises(
        ConfigurationError,
        match="Docker image is missing for build_policy='never': missing-prebuilt:latest",
    ):
        builder.build_and_run()

    assert calls == [("image_get", "missing-prebuilt:latest")]
    assert "Docker image rejected: tag=missing-prebuilt:latest policy=never reason=missing" in [
        record.getMessage() for record in caplog.records
    ]


def test_build_and_run_logs_existing_container_replacement(monkeypatch, caplog) -> None:
    calls: list[object] = []

    def build_image(_docker_client: Any, _image_spec: ImageSpec, _image_tag: str) -> object:
        calls.append("build")
        return object()

    def ensure_named_volumes(_docker_client: Any, _container_spec: ContainerSpec) -> None:
        calls.append("volumes")

    def run_container(_docker_client: Any, _container_spec: ContainerSpec) -> _Container:
        calls.append("run")
        return _Container()

    monkeypatch.setattr(container_builder_service, "build_image", build_image)
    monkeypatch.setattr(container_builder_service, "ensure_named_volumes", ensure_named_volumes)
    monkeypatch.setattr(container_builder_service, "run_container", run_container)
    caplog.set_level(logging.INFO, logger=container_builder_service.__name__)

    builder = ContainerBuilderService(plugins=[], container_name="existing-container")
    builder._docker_client = _DockerClientWithExistingContainer(calls)

    builder.build_and_run()

    assert ("remove", True) in calls
    assert "Removing existing container before start: name=existing-container" in [
        record.getMessage() for record in caplog.records
    ]


def test_minimal_builder_has_no_optional_container_features() -> None:
    _image_spec, container_spec = ContainerBuilderService(plugins=[])._prepare_specs()

    assert container_spec.name == "notes-assistant-opencode"
    assert container_spec.command is None
    assert container_spec.shm_size is None
    assert container_spec.volumes == {}
    assert container_spec.ports == {}
    assert container_spec.env == {}


def test_container_name_is_configurable() -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[],
        container_name="notes-assistant-opencode-worker-1",
    )._prepare_specs()

    assert container_spec.name == "notes-assistant-opencode-worker-1"


def test_full_plugin_composition_uses_public_services(tmp_path: Path) -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[
            LocalDirMountPluginService(tmp_path),
            OpenCodePersistencePluginService(config_volume="test_oc_config", data_volume="test_oc_data"),
            OpenCodeWebServerPluginService(host_port=4097),
        ]
    )._prepare_specs()

    assert container_spec.volumes[str(tmp_path)].target.as_posix() == "/workspace"
    assert container_spec.working_dir.as_posix() == "/workspace"
    assert container_spec.ports == {4096: 4097}
    assert container_spec.env["HOME"] == "/root"
    assert container_spec.command is not None
    assert "opencode web" in container_spec.command[2]
