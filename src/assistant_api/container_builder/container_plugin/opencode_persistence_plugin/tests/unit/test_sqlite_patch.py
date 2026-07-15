from __future__ import annotations

import pytest

from assistant_api.container_builder.container_plugin.opencode_persistence_plugin._sqlite_patch import (
    patch_bytes,
)
from assistant_api.container_builder.container_plugin.opencode_server_plugin._retry_patch import (
    patch_bytes as patch_retry_bytes,
)


WAL_CLIENT = b'run("PRAGMA journal_mode = WAL;")'
DELETE_CLIENT = b'run("PRAGMA journal_mode=DELETE")'
WAL_DATABASE = b'run("PRAGMA journal_mode = WAL"),'
QUERY_DATABASE = b'run("PRAGMA journal_mode      "),'
RETRY_SHAPE = (
    b"if(!e.data.isRetryable&&!(s!==void 0&&s>=500))return; "
    b"function p(o){return q.fromStepWithMetadata(x,(r,c,m)=>{"
    b"if(!r)return c.done(m.attempt);return {action:a.action,delay:1}}))}"
)


def _binary() -> bytes:
    return b"1.17.15 " + WAL_CLIENT + b" " + WAL_DATABASE + b" " + RETRY_SHAPE


def test_patch_replaces_both_wal_setters_with_delete_mode_and_query() -> None:
    patched = patch_bytes(_binary())

    assert len(patched) == len(_binary())
    assert patched.count(WAL_CLIENT) == 0
    assert patched.count(WAL_DATABASE) == 0
    assert patched.count(DELETE_CLIENT) == 1
    assert patched.count(QUERY_DATABASE) == 1


def test_patch_rejects_wrong_version() -> None:
    with pytest.raises(RuntimeError, match="version"):
        patch_bytes(_binary().replace(b"1.17.15", b"1.17.14"))


@pytest.mark.parametrize("missing", [WAL_CLIENT, WAL_DATABASE])
def test_patch_rejects_changed_or_duplicate_signatures(missing: bytes) -> None:
    with pytest.raises(RuntimeError, match="signature"):
        patch_bytes(_binary().replace(missing, b"changed"))

    with pytest.raises(RuntimeError, match="signature"):
        patch_bytes(_binary() + b" " + missing)


def test_sqlite_and_retry_patches_are_compatible_in_either_order() -> None:
    sqlite_then_retry = patch_retry_bytes(patch_bytes(_binary()), max_retries=2)
    retry_then_sqlite = patch_bytes(patch_retry_bytes(_binary(), max_retries=2))

    for patched in (sqlite_then_retry, retry_then_sqlite):
        assert DELETE_CLIENT in patched
        assert QUERY_DATABASE in patched
        assert b"attempt>2" in patched
