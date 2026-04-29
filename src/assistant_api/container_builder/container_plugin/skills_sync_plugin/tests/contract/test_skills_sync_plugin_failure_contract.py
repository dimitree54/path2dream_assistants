from __future__ import annotations

from pathlib import Path

import pytest

from skills_sync_contract_helpers import (
    assert_no_installed_artifacts,
    clone_live_repo,
    clone_live_repo_with_conflicting_bundles,
    only_startup_task,
    opencode_config_dir,
    prepare_container,
    run_startup_task,
    service_class,
    write_file,
)


def test_missing_live_bundle_fails_fast_without_partial_install(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    config_dir = opencode_config_dir(home)
    container_spec = prepare_container(["bundle-that-does-not-exist"])

    result = run_startup_task(only_startup_task(container_spec), home=home)

    assert result.exit_code != 0
    assert "bundle-that-does-not-exist" in result.output
    assert_no_installed_artifacts(config_dir)


@pytest.mark.parametrize(
    ("plugin_names", "relative_path", "content", "expected_error_fragment"),
    [
        (["yid-notes-assistant"], "AGENTS.md", "existing root rules", "AGENTS.md"),
        (["yid-notes-assistant"], "opencode.json", "{}", "opencode.json"),
        (
            ["path2dream-doppler-env"],
            "agents/env-manager.md",
            "existing agent",
            "agents/env-manager.md",
        ),
        (
            ["yid-notes-assistant"],
            "skills/send-files-to-user/SKILL.md",
            "existing skill",
            "skills/send-files-to-user",
        ),
    ],
)
def test_existing_system_artifacts_fail_by_upstream_installer_without_replace(
    tmp_path: Path,
    plugin_names: list[str],
    relative_path: str,
    content: str,
    expected_error_fragment: str,
) -> None:
    repo = clone_live_repo(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    config_dir = opencode_config_dir(home)
    existing_artifact = config_dir / relative_path
    write_file(existing_artifact, content)
    container_spec = prepare_container(
        plugin_names,
        repo_url=str(repo),
    )

    result = run_startup_task(only_startup_task(container_spec), home=home)

    assert result.exit_code != 0
    assert "refusing to overwrite" in result.output
    assert expected_error_fragment in result.output
    assert existing_artifact.read_text(encoding="utf-8") == content
    assert not (config_dir / "agents_backup").exists()
    assert list(config_dir.glob("AGENTS.md.backup*")) == []


def test_selected_agent_file_conflict_fails_fast_without_partial_install(
    tmp_path: Path,
) -> None:
    service_class()
    repo = clone_live_repo_with_conflicting_bundles(
        tmp_path,
        agent_file_name="shared-agent.md",
    )
    home = tmp_path / "home"
    home.mkdir()
    config_dir = opencode_config_dir(home)
    container_spec = prepare_container(
        ["contract-conflict-one", "contract-conflict-two"],
        repo_url=str(repo),
    )

    result = run_startup_task(only_startup_task(container_spec), home=home)

    assert result.exit_code != 0
    assert "Conflicting agent files" in result.output
    assert "shared-agent.md" in result.output
    assert_no_installed_artifacts(config_dir)


def test_selected_skill_entry_conflict_fails_fast_without_partial_install(
    tmp_path: Path,
) -> None:
    service_class()
    repo = clone_live_repo_with_conflicting_bundles(
        tmp_path,
        skill_name="shared-skill",
    )
    home = tmp_path / "home"
    home.mkdir()
    config_dir = opencode_config_dir(home)
    container_spec = prepare_container(
        ["contract-conflict-one", "contract-conflict-two"],
        repo_url=str(repo),
    )

    result = run_startup_task(only_startup_task(container_spec), home=home)

    assert result.exit_code != 0
    assert "Conflicting skill entries" in result.output
    assert "shared-skill" in result.output
    assert_no_installed_artifacts(config_dir)
