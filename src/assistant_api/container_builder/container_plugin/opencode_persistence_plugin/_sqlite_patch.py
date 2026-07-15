from __future__ import annotations

import argparse
from pathlib import Path


EXPECTED_VERSION = b"1.17.15"
WAL_CLIENT = b'run("PRAGMA journal_mode = WAL;")'
DELETE_CLIENT = b'run("PRAGMA journal_mode=DELETE")'
WAL_DATABASE = b'run("PRAGMA journal_mode = WAL"),'
QUERY_DATABASE = b'run("PRAGMA journal_mode      "),'


def patch_bytes(binary: bytes) -> bytes:
    if EXPECTED_VERSION not in binary:
        raise RuntimeError("unsupported OpenCode version; expected 1.17.15")
    _require_single_signature(binary, WAL_CLIENT, "SQLite client WAL initializer")
    _require_single_signature(binary, WAL_DATABASE, "database WAL initializer")

    patched = binary.replace(WAL_CLIENT, DELETE_CLIENT, 1)
    patched = patched.replace(WAL_DATABASE, QUERY_DATABASE, 1)
    if len(patched) != len(binary):
        raise RuntimeError("SQLite journal patch changed guarded binary width")
    return patched


def _require_single_signature(binary: bytes, signature: bytes, name: str) -> None:
    if binary.count(signature) != 1:
        raise RuntimeError(f"unsupported OpenCode compiled-code signature: {name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    args = parser.parse_args()
    original = args.binary.read_bytes()
    args.binary.write_bytes(patch_bytes(original))
    print(
        "OPENCODE_SQLITE_PATCH installed: version=1.17.15 "
        "host_history_journal=delete wal=disabled"
    )


if __name__ == "__main__":
    main()
