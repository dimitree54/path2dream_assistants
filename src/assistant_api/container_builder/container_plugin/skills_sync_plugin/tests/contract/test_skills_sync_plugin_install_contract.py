from __future__ import annotations

from pathlib import Path

from skills_sync_contract_helpers import (
    assert_startup_task_succeeds,
    only_startup_task,
    opencode_config_dir,
    prepare_container,
)


def test_live_default_repo_installs_selected_notes_assistant_bundle(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    container_spec = prepare_container(["yid-notes-assistant"], workspace)

    assert_startup_task_succeeds(only_startup_task(container_spec), home=home)

    config_dir = opencode_config_dir(home)
    agents_md = config_dir / "AGENTS.md"
    opencode_json = config_dir / "opencode.json"
    send_files_skill = config_dir / "skills" / "send-files-to-user" / "SKILL.md"
    assert agents_md.exists()
    assert opencode_json.exists()
    assert send_files_skill.exists()
    assert "documents" in agents_md.read_text(encoding="utf-8")
    assert "gpt-5.5" in opencode_json.read_text(encoding="utf-8")
    assert "outbox" in send_files_skill.read_text(encoding="utf-8")
    assert not (workspace / "AGENTS.md").exists()
    assert not (workspace / ".opencode").exists()


def test_live_default_repo_installs_multiple_bundles_agents_skills_and_rules(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    container_spec = prepare_container(
        ["yid-notes-assistant", "path2dream-doppler-env"],
    )

    assert_startup_task_succeeds(only_startup_task(container_spec), home=home)

    config_dir = opencode_config_dir(home)
    agents_md = (config_dir / "AGENTS.md").read_text(encoding="utf-8")
    assert "documents" in agents_md
    assert "Secrets management" in agents_md
    assert (config_dir / "opencode.json").exists()
    assert (config_dir / "agents" / "env-manager.md").exists()
    assert (config_dir / "skills" / "send-files-to-user" / "SKILL.md").exists()
    assert (
        config_dir
        / "skills"
        / "working-with-env-vars-and-secrets"
        / "SKILL.md"
    ).exists()
    assert (
        config_dir
        / "skills"
        / "working-with-env-vars-and-secrets"
        / "references"
        / "set-up-telegram-bot"
        / "SKILL.md"
    ).exists()
