from __future__ import annotations

import base64
import inspect
import re
from pathlib import Path, PurePosixPath

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder._errors import ConfigurationError
from assistant_api.models import ContainerExecutionIdentity, ContainerRuntimeContext
from command_monitor_contract_helpers import RecordingContainer, service_class


LOG_VOLUME = "contract_command_monitor_logs"
PLUGIN_IMAGE_PATH = "/opt/notes-assistant-api/command_monitor_plugin.js"
OPENCODE_PLUGIN_FILE_NAME = "notes-assistant-command-monitor.js"
LOG_DIR = "/tmp/notes-assistant/command-monitor"
LOG_FILE = f"{LOG_DIR}/failed-commands.jsonl"


def test_public_service_import_and_init_signature() -> None:
    service = service_class()
    signature = inspect.signature(service)

    assert service.__name__ == "CommandMonitorPluginService"
    assert list(signature.parameters) == ["log_volume", "log_host_dir"]


def test_plugin_name() -> None:
    plugin = service_class()(log_volume=LOG_VOLUME)

    assert plugin.name == "command-monitor"


@pytest.mark.parametrize("log_volume", ["", "   ", " padded ", 123])
def test_init_rejects_invalid_log_volume(log_volume: object) -> None:
    with pytest.raises(ConfigurationError, match="log_volume"):
        service_class()(log_volume=log_volume)


def test_init_requires_exactly_one_persistence_source(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="exactly one"):
        service_class()()
    with pytest.raises(ConfigurationError, match="exactly one"):
        service_class()(log_volume=LOG_VOLUME, log_host_dir=tmp_path)
    for invalid in ("", " padded ", 123):
        with pytest.raises(ConfigurationError, match="log_host_dir"):
            service_class()(log_host_dir=invalid)


def test_configure_image_declares_python3_and_embeds_plugin_source() -> None:
    image_spec, _container_spec = ContainerBuilderService(
        plugins=[service_class()(log_volume=LOG_VOLUME)]
    )._prepare_specs()

    assert "python3" in image_spec.apk_packages
    embed_commands = [
        command for command in image_spec.run_commands if PLUGIN_IMAGE_PATH in command
    ]
    assert embed_commands, "plugin source embedding commands are missing"
    assert not any(
        "apk add" in command or "pip install" in command for command in embed_commands
    )


def test_embedded_plugin_source_matches_module_js_file() -> None:
    image_spec, _container_spec = ContainerBuilderService(
        plugins=[service_class()(log_volume=LOG_VOLUME)]
    )._prepare_specs()

    chunks = re.findall(
        r"base64\.b64decode\('([^']+)'\)",
        "\n".join(image_spec.run_commands),
    )
    assert chunks, "base64 plugin source chunks are missing"
    embedded = base64.b64decode("".join(chunks))

    source = _module_js_source()
    assert embedded == source.encode("utf-8")


def test_plugin_js_source_declares_documented_monitoring_contract() -> None:
    source = _module_js_source()

    assert '"tool.execute.after"' in source
    assert 'input.tool !== "bash"' in source
    assert LOG_FILE in source or (
        LOG_DIR in source and "failed-commands.jsonl" in source
    )
    assert "metadata" in source
    assert "exit === 0" in source
    assert "appendFileSync" in source
    for field in (
        "timestamp",
        "sessionID",
        "callID",
        "command",
        "description",
        "workdir",
        "exit",
        "output_tail",
    ):
        assert field in source
    assert "4000" in source


def test_configure_container_mounts_log_volume() -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[service_class()(log_volume=LOG_VOLUME)]
    )._prepare_specs()

    mount = container_spec.volumes[LOG_VOLUME]
    assert mount.source == LOG_VOLUME
    assert mount.target == PurePosixPath(LOG_DIR)
    assert mount.type == "volume"
    assert mount.mode == "rw"


def test_execution_identity_requires_compatible_host_log_directory(tmp_path: Path) -> None:
    identity = ContainerExecutionIdentity(
        uid=tmp_path.stat().st_uid,
        gid=tmp_path.stat().st_gid,
        umask=0o022,
    )
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[service_class()(log_host_dir=tmp_path)],
        execution_identity=identity,
    )._prepare_specs()

    mount = container_spec.volumes[str(tmp_path)]
    assert mount.type == "bind"
    assert mount.target == PurePosixPath(LOG_DIR)

    with pytest.raises(ConfigurationError, match="log_host_dir"):
        ContainerBuilderService(
            plugins=[service_class()(log_volume=LOG_VOLUME)],
            execution_identity=identity,
        )._prepare_specs()


def test_configure_container_registers_install_startup_task() -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[service_class()(log_volume=LOG_VOLUME)]
    )._prepare_specs()

    assert len(container_spec.startup_tasks) == 1
    task = container_spec.startup_tasks[0]
    assert task.name == "install-command-monitor-plugin"
    assert task.owner_plugin_name == "command-monitor"
    assert task.command[:2] == ["/bin/sh", "-lc"]
    script = task.command[2]
    assert '${XDG_CONFIG_HOME:?' in script
    assert PLUGIN_IMAGE_PATH in script
    assert OPENCODE_PLUGIN_FILE_NAME in script
    assert "/opencode/plugins" in script
    assert f"mkdir -p {LOG_DIR}" in script


def test_configure_container_does_not_touch_command_ports_env() -> None:
    _image_spec, container_spec = ContainerBuilderService(
        plugins=[service_class()(log_volume=LOG_VOLUME)]
    )._prepare_specs()

    assert container_spec.command is None
    assert container_spec.ports == {}
    assert container_spec.env == {}
    assert container_spec.managed_processes == []


def test_post_start_checks_plugin_file_and_log_dir() -> None:
    plugin = service_class()(log_volume=LOG_VOLUME)
    container = RecordingContainer()

    plugin.post_start(
        ContainerRuntimeContext(
            docker_client=object(),
            container=container,
            state={},
        )
    )

    assert len(container.commands) == 1
    script = container.commands[0][2]
    assert OPENCODE_PLUGIN_FILE_NAME in script
    assert LOG_DIR in script


def test_post_start_fails_when_health_check_fails() -> None:
    plugin = service_class()(log_volume=LOG_VOLUME)
    container = RecordingContainer(exit_code=1, output=b"plugin file missing")

    with pytest.raises(RuntimeError, match="command monitor health check failed"):
        plugin.post_start(
            ContainerRuntimeContext(
                docker_client=object(),
                container=container,
                state={},
            )
        )


def _module_js_source() -> str:
    module_dir = (
        Path(__file__).resolve().parents[2]
    )
    return (module_dir / "_command_monitor_plugin.js").read_text(encoding="utf-8")
