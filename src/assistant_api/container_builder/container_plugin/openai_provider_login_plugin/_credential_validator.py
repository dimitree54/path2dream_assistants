from __future__ import annotations

import json
import threading
import urllib.parse
from collections.abc import Callable
from typing import Any


class OpenAICredentialValidationError(RuntimeError):
    pass


class OpenAICredentialValidator:
    def __init__(
        self,
        opencode_model: str,
        request_json: Callable[..., Any],
    ) -> None:
        self._opencode_model = opencode_model
        self._request_json = request_json
        self._lock = threading.Lock()
        self._validated_record: str | None = None
        self._validated_result: bool | None = None

    def validate(self, auth_record: dict[str, Any]) -> bool:
        with self._lock:
            fingerprint = json.dumps(auth_record, sort_keys=True, separators=(",", ":"))
            expires = float(auth_record["expires"])
            if fingerprint == self._validated_record:
                if self._validated_result is False:
                    return False
                if self._validated_result is True and _current_time_ms() < expires:
                    return True
            result = self._probe()
            self._validated_record = fingerprint
            self._validated_result = result
            return result

    def _probe(self) -> bool:
        session = self._request_json(
            "POST",
            "/session",
            "OpenCode credential probe session creation failed",
            {"title": "OpenAI credential validation"},
        )
        session_id = session.get("id") if isinstance(session, dict) else None
        if not isinstance(session_id, str) or not session_id:
            raise OpenAICredentialValidationError(
                "OpenCode credential probe session has no id"
            )
        provider_id, model_id = self._opencode_model.split("/", 1)
        session_path = f"/session/{urllib.parse.quote(session_id, safe='')}"
        try:
            response = self._request_json(
                "POST",
                f"{session_path}/message",
                "OpenCode credential probe request failed",
                {
                    "model": {"providerID": provider_id, "modelID": model_id},
                    "tools": {},
                    "parts": [{"type": "text", "text": "Reply exactly OK."}],
                },
                timeout=60,
            )
        finally:
            self._request_json(
                "DELETE",
                session_path,
                "OpenCode credential probe session cleanup failed",
            )
        error = response.get("info", {}).get("error") if isinstance(response, dict) else None
        if error is None:
            return True
        data = error.get("data") if isinstance(error, dict) else None
        status_code = data.get("statusCode") if isinstance(data, dict) else None
        if status_code in {401, 403}:
            return False
        raise OpenAICredentialValidationError(
            f"OpenCode credential probe failed with provider status {status_code!r}"
        )


def _current_time_ms() -> float:
    import time

    return time.time() * 1000
