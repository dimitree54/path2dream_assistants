from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import cast

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.container_builder.container_plugin.container_environment_plugin import (
    ContainerEnvironmentPluginService,
)
from assistant_api.models import ContainerSpec


def test_public_service_import_and_init_signature() -> None:
    signature = inspect.signature(ContainerEnvironmentPluginService)

    assert list(signature.parameters) == ["environment"]


def test_configure_container_adds_exact_environment_without_changing_image() -> None:
    plugin = ContainerEnvironmentPluginService(
        environment={
            "OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS": "600000",
            "EMPTY_VALUE": "",
        }
    )

    image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()

    assert container_spec.env == {
        "OPENCODE_EXPERIMENTAL_BASH_DEFAULT_TIMEOUT_MS": "600000",
        "EMPTY_VALUE": "",
    }
    assert image_spec.env == {}
    assert image_spec.apk_packages == []
    assert image_spec.python_packages == []
    assert image_spec.run_commands == ["mkdir -p /workspace"]


def test_init_defensively_copies_environment() -> None:
    environment = {"ORIGINAL": "value"}
    plugin = ContainerEnvironmentPluginService(environment=environment)
    environment["ORIGINAL"] = "changed"
    environment["ADDED_LATER"] = "ignored"

    _image_spec, container_spec = ContainerBuilderService(plugins=[plugin])._prepare_specs()

    assert container_spec.env == {"ORIGINAL": "value"}


@pytest.mark.parametrize(
    "environment",
    [None, [], [("VALID_NAME", "value")], "VALID_NAME=value"],
)
def test_init_rejects_non_mapping_environment(environment: object) -> None:
    with pytest.raises(ConfigurationError, match="environment must be a mapping"):
        ContainerEnvironmentPluginService(
            environment=cast(Mapping[str, str], environment)
        )


@pytest.mark.parametrize(
    "name",
    ["", "1INVALID", "INVALID-NAME", "INVALID NAME", "INVALID.NAME", 123],
)
def test_init_rejects_invalid_environment_name(name: object) -> None:
    with pytest.raises(ConfigurationError, match="valid environment variable name"):
        ContainerEnvironmentPluginService(
            environment=cast(Mapping[str, str], {name: "value"})
        )


@pytest.mark.parametrize("value", [None, 123, True, b"bytes"])
def test_init_rejects_non_string_environment_value(value: object) -> None:
    with pytest.raises(ConfigurationError, match="VALUE.*string"):
        ContainerEnvironmentPluginService(
            environment=cast(Mapping[str, str], {"VALUE": value})
        )


def test_configure_container_accepts_identical_existing_value() -> None:
    container = ContainerSpec(
        name="test",
        image_tag="test:latest",
        env={"SHARED": "same"},
    )

    ContainerEnvironmentPluginService(environment={"SHARED": "same"}).configure_container(
        container
    )

    assert container.env == {"SHARED": "same"}


def test_configure_container_rejects_conflict_atomically_without_values() -> None:
    container = ContainerSpec(
        name="test",
        image_tag="test:latest",
        env={"CONFLICT": "existing-secret"},
    )
    plugin = ContainerEnvironmentPluginService(
        environment={"ADDED_FIRST": "new-value", "CONFLICT": "new-secret"}
    )

    with pytest.raises(ConfigurationError, match="CONFLICT") as error:
        plugin.configure_container(container)

    assert container.env == {"CONFLICT": "existing-secret"}
    assert "existing-secret" not in str(error.value)
    assert "new-secret" not in str(error.value)


def test_service_never_reads_host_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOST_ONLY_VALUE", "must-not-leak")

    _image_spec, container_spec = ContainerBuilderService(
        plugins=[ContainerEnvironmentPluginService(environment={"EXPLICIT": "value"})]
    )._prepare_specs()

    assert container_spec.env == {"EXPLICIT": "value"}


def test_service_never_logs_environment_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    plugin = ContainerEnvironmentPluginService(environment={"SECRET": "do-not-log"})

    ContainerBuilderService(plugins=[plugin])._prepare_specs()

    assert "do-not-log" not in caplog.text
