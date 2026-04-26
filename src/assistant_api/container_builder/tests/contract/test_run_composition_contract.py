from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from assistant_api.container_builder.container_plugin.google_drive_mount_plugin import (
    GoogleDriveMountPluginService,
)
from assistant_api.container_builder.container_plugin.google_drive_persistence_plugin import (
    GoogleDrivePersistencePluginService,
)


def test_production_run_composes_google_drive_persistence_before_mount(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON",
        json.dumps({"web": {"client_id": "client-id", "client_secret": "client-secret"}}),
    )
    run_module = _load_run_module()

    builder = run_module.create_builder()
    _image_spec, container_spec = builder._prepare_specs()
    plugins = builder.plugins

    persistence_index = _plugin_index(plugins, GoogleDrivePersistencePluginService)
    mount_index = _plugin_index(plugins, GoogleDriveMountPluginService)

    assert persistence_index < mount_index
    assert any(task.name == "google-drive-mount-restore" for task in container_spec.startup_tasks)
    assert all(
        "/workspace/notes/inbox" not in " ".join(task.command)
        and "/workspace/notes/outbox" not in " ".join(task.command)
        for task in container_spec.startup_tasks
    )


def _load_run_module() -> ModuleType:
    run_path = Path(__file__).parents[5] / "scripts" / "run.py"
    spec = importlib.util.spec_from_file_location("notes_assistant_run_script", run_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _plugin_index(plugins: list[object], plugin_type: type[object]) -> int:
    return next(index for index, plugin in enumerate(plugins) if isinstance(plugin, plugin_type))
