from __future__ import annotations

import pytest

from assistant_api.container_builder.container_plugin.opencode_server_plugin._retry_patch import (
    patch_bytes,
)


def test_patch_rejects_wrong_version() -> None:
    with pytest.raises(RuntimeError, match="version"):
        patch_bytes(b"1.17.14", max_retries=5)


def test_patch_rejects_unknown_compiled_shape() -> None:
    with pytest.raises(RuntimeError, match="signature"):
        patch_bytes(b"1.17.15", max_retries=5)
