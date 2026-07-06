from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

import pytest

from assistant_api.container_builder import ContainerBuilderService
from assistant_api.container_builder.container_plugin.local_dir_mount_plugin import (
    LocalDirMountPluginService,
)
from assistant_api.container_builder.container_plugin.local_skills_plugin import (
    LocalSkillsPluginService,
)
from assistant_api.container_builder.container_plugin.opencode_persistence_plugin import (
    OpenCodePersistencePluginService,
)
from assistant_api.container_builder.container_plugin.opencode_server_plugin import (
    OpenCodeServerPluginService,
)
from assistant_api.container_builder.container_plugin.video_processing_plugin import (
    VideoProcessingPluginService,
)
from assistant_api.models import LocalSkillPostInstallCommand
from local_skills_contract_helpers import available_tcp_port, make_source


@pytest.mark.live_container
def test_live_container_installs_local_skill_before_opencode_starts(
    tmp_path: Path,
) -> None:
    source = make_source(tmp_path / "private-artifacts")
    _write_npm_probe_project(source / ".opencode" / "skills" / "private-skill")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    host_port = available_tcp_port()
    builder = ContainerBuilderService(
        plugins=[
            OpenCodePersistencePluginService(
                config_volume="unused_local_skills_config",
                data_volume="unused_local_skills_data",
                persist_auth=False,
                persist_chat_history=False,
                persist_opencode_artifacts=False,
                persist_skills=False,
                persist_agents=False,
            ),
            LocalDirMountPluginService(workspace),
            VideoProcessingPluginService(),
            LocalSkillsPluginService(
                source,
                post_install_commands=[
                    LocalSkillPostInstallCommand(
                        name="install-private-skill-npm-deps",
                        working_dir=PurePosixPath("skills/private-skill"),
                        command=[
                            "/bin/sh",
                            "-lc",
                            (
                                "npm ci --no-audit --no-fund --loglevel=error "
                                "&& printf '%s\\n' local-skill-post-install-prepared "
                                "> postinstall.marker"
                            ),
                        ],
                    )
                ],
            ),
            OpenCodeServerPluginService(host_port=host_port),
        ],
        container_name=f"notes-assistant-local-skills-test-{os.getpid()}",
    )

    try:
        running = builder.build_and_run()
    except Exception as error:
        _stop_builder_if_started(builder)
        pytest.fail(
            "local skills plugin must install artifacts before OpenCode starts; "
            f"got {type(error).__name__}: {error}\n\n{_docker_build_log(error)}"
        )

    try:
        result = running.container.exec_run(["/bin/sh", "-lc", _probe_script()])
        output = _decode_output(result.output)
        assert result.exit_code == 0, output
        assert "local-skills-live-probe-ok" in output
    finally:
        builder.stop(remove=True)


def _probe_script() -> str:
    return "\n".join(
        [
            "set -eu",
            "test -r /root/.config/opencode/skills/private-skill/SKILL.md",
            "grep -q 'private skill marker' /root/.config/opencode/skills/private-skill/SKILL.md",
            "test -r /root/.config/opencode/skills/private-skill/references/notes.md",
            "test -r /root/.config/opencode/skills/private-skill/package-lock.json",
            "grep -q local-skill-post-install-prepared /root/.config/opencode/skills/private-skill/postinstall.marker",
            "test -r /root/.config/opencode/agents/private-agent.md",
            "test -r /root/.config/opencode/AGENTS.md",
            "test -r /root/.config/opencode/opencode.json",
            "test ! -e /workspace/.opencode",
            "test ! -e /workspace/AGENTS.md",
            (
                "health_response=$(wget -q -T 5 -O - "
                "http://127.0.0.1:4096/global/health 2>/dev/null) "
                "&& case \"$health_response\" in "
                "*'\"healthy\":true'*|*'\"healthy\": true'*) true ;; *) false ;; esac"
            ),
            "printf '%s\\n' local-skills-live-probe-ok",
        ]
    )


def _decode_output(output: bytes | str) -> str:
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output


def _stop_builder_if_started(builder: ContainerBuilderService) -> None:
    try:
        builder.stop(remove=True)
    except Exception:
        return None


def _docker_build_log(error: BaseException) -> str:
    build_log = getattr(error, "build_log", None)
    if not build_log:
        return "<docker build log is not available>"

    lines: list[str] = []
    for entry in _iter_build_log_entries(build_log):
        if isinstance(entry, dict):
            line = entry.get("stream") or entry.get("error") or repr(entry)
        else:
            line = repr(entry)
        lines.append(line.rstrip())
    return "\n".join(lines)


def _iter_build_log_entries(build_log: object) -> Iterable[object]:
    if isinstance(build_log, Iterable):
        return build_log
    return []


def _write_npm_probe_project(skill_dir: Path) -> None:
    (skill_dir / "package.json").write_text(
        "\n".join(
            [
                "{",
                '  "name": "local-skill-post-install-probe",',
                '  "version": "1.0.0",',
                '  "private": true',
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (skill_dir / "package-lock.json").write_text(
        "\n".join(
            [
                "{",
                '  "name": "local-skill-post-install-probe",',
                '  "version": "1.0.0",',
                '  "lockfileVersion": 3,',
                '  "requires": true,',
                '  "packages": {',
                '    "": {',
                '      "name": "local-skill-post-install-probe",',
                '      "version": "1.0.0"',
                "    }",
                "  }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
