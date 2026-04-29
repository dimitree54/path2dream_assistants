from __future__ import annotations

import importlib.util
import json
import builtins
import sys
from pathlib import Path, PurePosixPath
from types import ModuleType

from assistant_api.container_builder._dockerfile import render_dockerfile
from assistant_api.container_builder.container_plugin.google_drive_mount_plugin import (
    GoogleDriveMountPluginService,
)
from assistant_api.container_builder.container_plugin.google_drive_persistence_plugin import (
    GoogleDrivePersistencePluginService,
)
from assistant_api.container_builder.container_plugin.openai_provider_login_plugin import (
    OpenAIProviderLoginPluginService,
)
from assistant_api.container_builder.container_plugin.opencode_persistence_plugin import (
    OpenCodePersistencePluginService,
)
from assistant_api.container_builder.container_plugin.skills_sync_plugin import (
    SkillsSyncPluginService,
)


def test_run_module_import_does_not_fetch_doppler_env(
    monkeypatch,
) -> None:
    original_import = builtins.__import__
    sys.modules.pop("doppler_env", None)

    def guarded_import(name: str, *args, **kwargs):
        if name == "doppler_env":
            raise AssertionError("doppler_env must only load from main()")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    _load_run_module()


def test_production_run_composes_google_drive_persistence_before_mount(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "GOOGLE_OAUTH_CLIENT_CREDENTIALS_JSON",
        json.dumps(
            {"web": {"client_id": "client-id", "client_secret": "client-secret"}}
        ),
    )
    run_module = _load_run_module()

    builder = run_module.create_builder()
    image_spec, container_spec = builder._prepare_specs()
    plugins = builder.plugins

    persistence_index = _plugin_index(plugins, GoogleDrivePersistencePluginService)
    mount_index = _plugin_index(plugins, GoogleDriveMountPluginService)
    opencode_persistence_index = _plugin_index(plugins, OpenCodePersistencePluginService)
    skills_index = _plugin_index(plugins, SkillsSyncPluginService)
    openai_index = _plugin_index(plugins, OpenAIProviderLoginPluginService)
    opencode_persistence = plugins[opencode_persistence_index]

    assert persistence_index < mount_index
    assert isinstance(opencode_persistence, OpenCodePersistencePluginService)
    assert opencode_persistence.persist_auth is True
    assert opencode_persistence.persist_chat_history is True
    assert opencode_persistence.persist_opencode_artifacts is False
    assert opencode_persistence.persist_skills is False
    assert opencode_persistence.persist_agents is False
    assert "notes_assistant_api_opencode_config" not in container_spec.volumes
    assert "notes_assistant_api_opencode_data" not in container_spec.volumes
    assert "notes_assistant_api_opencode_data_auth" in container_spec.volumes
    assert "notes_assistant_api_opencode_data_history" in container_spec.volumes
    assert all(
        volume.target != PurePosixPath("/root/.config/opencode")
        for volume in container_spec.volumes.values()
    )
    assert skills_index < openai_index
    assert any(
        task.name == "google-drive-mount-restore"
        for task in container_spec.startup_tasks
    )
    startup_task_names = [task.name for task in container_spec.startup_tasks]
    skills_task_index = startup_task_names.index("install-opencode-artifact-bundles")
    openai_task_index = startup_task_names.index("openai-opencode-default-model")
    assert skills_task_index < openai_task_index
    assert all(
        "/workspace/notes/inbox" not in " ".join(task.command)
        and "/workspace/notes/outbox" not in " ".join(task.command)
        for task in container_spec.startup_tasks
    )
    assert all("apk add" not in command for command in image_spec.run_commands)
    assert all("pip install" not in command for command in image_spec.run_commands)
    assert {"git", "python3", "py3-pip", "rclone", "fuse3"} <= set(
        image_spec.apk_packages
    )
    assert {"fastapi", "uvicorn", "python-multipart"} <= set(
        image_spec.python_packages
    )
    dockerfile = render_dockerfile(image_spec)
    assert dockerfile.count("apk add --no-cache") == 1
    assert dockerfile.count("python3 -m pip install --break-system-packages") == 1


def _load_run_module() -> ModuleType:
    run_path = Path(__file__).parents[5] / "scripts" / "run.py"
    spec = importlib.util.spec_from_file_location(
        "notes_assistant_run_script",
        run_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _plugin_index(plugins: list[object], plugin_type: type[object]) -> int:
    return next(
        index for index, plugin in enumerate(plugins) if isinstance(plugin, plugin_type)
    )
