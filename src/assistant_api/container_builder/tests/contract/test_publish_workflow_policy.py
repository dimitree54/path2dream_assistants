from __future__ import annotations

import re
from pathlib import Path


def test_publish_workflow_requires_live_container_gate_before_build() -> None:
    workflow = _repo_root() / ".github" / "workflows" / "publish.yml"
    text = workflow.read_text(encoding="utf-8")

    assert re.search(r"(?m)^  live-container:\n", text)
    assert 'uv run pytest -m "live_container"' in text
    assert "Verify Docker FUSE support" in text
    assert "/dev/fuse" in text
    assert "rclone mount" in text

    build_match = re.search(r"(?ms)^  build:\n(?P<block>.*?)(?=^  publish:)", text)
    assert build_match is not None
    build_block = build_match.group("block")
    assert re.search(r"(?m)^    needs:\n", build_block)
    assert re.search(r"(?m)^      - test$", build_block)
    assert re.search(r"(?m)^      - live-container$", build_block)


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if parent.joinpath(".github", "workflows", "publish.yml").exists():
            return parent
    raise AssertionError("Could not find repository root from test path")
