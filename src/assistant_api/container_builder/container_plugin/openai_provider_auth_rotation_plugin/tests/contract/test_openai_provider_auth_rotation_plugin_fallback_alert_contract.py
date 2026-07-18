from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from assistant_api.container_builder.container_plugin.openai_provider_auth_rotation_plugin import (
    openai_provider_auth_rotation_plugin_service as plugin_module,
)
from assistant_api.container_builder.container_plugin.openai_provider_auth_rotation_plugin._auth_rotation import (
    AUTH_ROTATION_RESULT_CANDIDATE,
    AUTH_ROTATION_RESULT_FALLBACK,
    AUTH_ROTATION_RESULT_PATH,
)
from assistant_api.models import ContainerRuntimeContext
from openai_provider_auth_rotation_contract_helpers import (
    api_auth,
    service_class,
    write_auth_file,
)


class _FakeContainer:
    def __init__(self, result_text: str | None, *, exit_code: int = 0) -> None:
        self._result_text = result_text
        self._exit_code = exit_code

    def exec_run(self, command: list[str]) -> Any:
        assert AUTH_ROTATION_RESULT_PATH.as_posix() in " ".join(command)
        if self._result_text is None:
            return _ExecResult(exit_code=1, output=b"")
        return _ExecResult(
            exit_code=self._exit_code,
            output=self._result_text.encode("utf-8"),
        )


class _ExecResult:
    def __init__(self, *, exit_code: int, output: bytes) -> None:
        self.exit_code = exit_code
        self.output = output


def _runtime(result_text: str | None, *, exit_code: int = 0) -> ContainerRuntimeContext:
    return ContainerRuntimeContext(
        docker_client=object(),
        container=_FakeContainer(result_text, exit_code=exit_code),
        state={},
    )


def test_post_start_alerts_when_fallback_api_token_was_used(tmp_path: Path) -> None:
    auth_file = write_auth_file(tmp_path / "auth.json", api_auth())
    alerts: list[str] = []
    plugin = service_class()([auth_file], on_auth_alert=alerts.append)

    plugin.post_start(_runtime(AUTH_ROTATION_RESULT_FALLBACK))

    assert alerts == [plugin_module.FALLBACK_AUTH_ALERT_MESSAGE]
    assert all("sk-" not in alert for alert in alerts)


def test_post_start_does_not_alert_when_candidate_succeeded(tmp_path: Path) -> None:
    auth_file = write_auth_file(tmp_path / "auth.json", api_auth())
    alerts: list[str] = []
    plugin = service_class()([auth_file], on_auth_alert=alerts.append)

    plugin.post_start(_runtime(AUTH_ROTATION_RESULT_CANDIDATE))

    assert alerts == []


def test_post_start_swallows_alert_callback_failures(tmp_path: Path) -> None:
    auth_file = write_auth_file(tmp_path / "auth.json", api_auth())

    def boom(_message: str) -> None:
        raise RuntimeError("alert delivery failed")

    plugin = service_class()([auth_file], on_auth_alert=boom)
    plugin.post_start(_runtime(AUTH_ROTATION_RESULT_FALLBACK))


def test_post_start_without_callback_is_noop(tmp_path: Path) -> None:
    auth_file = write_auth_file(tmp_path / "auth.json", api_auth())
    plugin = service_class()([auth_file])
    plugin.post_start(_runtime(AUTH_ROTATION_RESULT_FALLBACK))
