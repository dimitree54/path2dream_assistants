from __future__ import annotations

from pathlib import Path

from skills_sync_contract_helpers import (
    assert_startup_task_succeeds,
    only_startup_task,
    prepare_container,
)


def test_live_default_repo_installs_selected_notes_assistant_bundle(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    container_spec = prepare_container(["yid-notes-assistant"], target)

    assert_startup_task_succeeds(only_startup_task(container_spec))

    agents_md = target / "AGENTS.md"
    add_note = target / ".opencode" / "agents" / "add_note.md"
    ask_notes = target / ".opencode" / "agents" / "ask_notes.md"
    assert agents_md.exists()
    assert add_note.exists()
    assert ask_notes.exists()
    assert "documents" in agents_md.read_text(encoding="utf-8")
    assert "add-note" in add_note.read_text(encoding="utf-8")
    assert "ask-notes" in ask_notes.read_text(encoding="utf-8")


def test_live_default_repo_installs_multiple_bundles_agents_skills_and_rules(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    container_spec = prepare_container(
        ["yid-notes-assistant", "path2dream-doppler-env"],
        target,
    )

    assert_startup_task_succeeds(only_startup_task(container_spec))

    agents_md = (target / "AGENTS.md").read_text(encoding="utf-8")
    assert "documents" in agents_md
    assert "Secrets management" in agents_md
    assert (target / ".opencode" / "agents" / "add_note.md").exists()
    assert (target / ".opencode" / "agents" / "ask_notes.md").exists()
    assert (target / ".opencode" / "agents" / "env-manager.md").exists()
    assert (
        target
        / ".opencode"
        / "skills"
        / "working-with-env-vars-and-secrets"
        / "SKILL.md"
    ).exists()
    assert (
        target
        / ".opencode"
        / "skills"
        / "working-with-env-vars-and-secrets"
        / "references"
        / "set-up-telegram-bot"
        / "SKILL.md"
    ).exists()
