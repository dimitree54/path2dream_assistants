from __future__ import annotations

from pathlib import Path

import pytest

from skills_sync_contract_helpers import (
    assert_no_installed_artifacts,
    clone_live_repo_with_conflicting_bundles,
    only_startup_task,
    prepare_container,
    run_startup_task,
    service_class,
    write_file,
)


def test_missing_live_bundle_fails_fast_without_partial_install(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    container_spec = prepare_container(["bundle-that-does-not-exist"], target)

    result = run_startup_task(only_startup_task(container_spec))

    assert result.exit_code != 0
    assert "bundle-that-does-not-exist" in result.output
    assert_no_installed_artifacts(target)


@pytest.mark.parametrize(
    ("relative_path", "content", "expected_error_fragment"),
    [
        ("AGENTS.md", "existing root rules", "AGENTS.md"),
        (".opencode/agents/existing.md", "existing agent", ".opencode/agents"),
        (
            ".opencode/skills/existing-skill/SKILL.md",
            "existing skill",
            ".opencode/skills",
        ),
    ],
)
def test_existing_target_artifacts_fail_before_clone_without_backup_or_replace(
    tmp_path: Path,
    relative_path: str,
    content: str,
    expected_error_fragment: str,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    existing_artifact = target / relative_path
    write_file(existing_artifact, content)
    missing_repo_url = str(tmp_path / "missing-repo")
    container_spec = prepare_container(
        ["yid-notes-assistant"],
        target,
        repo_url=missing_repo_url,
    )

    result = run_startup_task(only_startup_task(container_spec))

    assert result.exit_code != 0
    assert expected_error_fragment in result.output
    assert "missing-repo" not in result.output
    assert existing_artifact.read_text(encoding="utf-8") == content
    assert not (target / "agents_backup").exists()
    assert list(target.glob("AGENTS.md.backup*")) == []


def test_selected_agent_file_conflict_fails_fast_without_partial_install(
    tmp_path: Path,
) -> None:
    service_class()
    repo = clone_live_repo_with_conflicting_bundles(
        tmp_path,
        agent_file_name="shared-agent.md",
    )
    target = tmp_path / "target"
    target.mkdir()
    container_spec = prepare_container(
        ["contract-conflict-one", "contract-conflict-two"],
        target,
        repo_url=str(repo),
    )

    result = run_startup_task(only_startup_task(container_spec))

    assert result.exit_code != 0
    assert "Conflicting agent files" in result.output
    assert "shared-agent.md" in result.output
    assert_no_installed_artifacts(target)


def test_selected_skill_entry_conflict_fails_fast_without_partial_install(
    tmp_path: Path,
) -> None:
    service_class()
    repo = clone_live_repo_with_conflicting_bundles(
        tmp_path,
        skill_name="shared-skill",
    )
    target = tmp_path / "target"
    target.mkdir()
    container_spec = prepare_container(
        ["contract-conflict-one", "contract-conflict-two"],
        target,
        repo_url=str(repo),
    )

    result = run_startup_task(only_startup_task(container_spec))

    assert result.exit_code != 0
    assert "Conflicting skill entries" in result.output
    assert "shared-skill" in result.output
    assert_no_installed_artifacts(target)
