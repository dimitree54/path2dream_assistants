from __future__ import annotations


def database_check_lines() -> list[str]:
    return [
        'history_db="$history_dir/opencode.db"',
        'if [ -e "$history_db" ]; then',
        "  set +e",
        (
            "  integrity_output=$(sqlite3 -readonly \"$history_db\" "
            "'PRAGMA integrity_check;' 2>&1)"
        ),
        "  integrity_status=$?",
        "  set -e",
        '  if [ "$integrity_status" -ne 0 ] || [ "$integrity_output" != "ok" ]; then',
        (
            "    printf 'OpenCode host-history integrity check failed: "
            "database=%s diagnostic=%s\\n' \"$history_db\" \"$integrity_output\" >&2"
        ),
        "    exit 1",
        "  fi",
        "  set +e",
        (
            "  journal_output=$(sqlite3 \"$history_db\" "
            "'PRAGMA journal_mode=DELETE;' 2>&1)"
        ),
        "  journal_status=$?",
        "  set -e",
        '  if [ "$journal_status" -ne 0 ] || [ "$journal_output" != "delete" ]; then',
        (
            "    printf 'OpenCode host-history journal configuration failed: "
            "database=%s diagnostic=%s\\n' \"$history_db\" \"$journal_output\" >&2"
        ),
        "    exit 1",
        "  fi",
        "  set +e",
        (
            "  final_integrity_output=$(sqlite3 -readonly \"$history_db\" "
            "'PRAGMA integrity_check;' 2>&1)"
        ),
        "  final_integrity_status=$?",
        "  set -e",
        (
            '  if [ "$final_integrity_status" -ne 0 ] || '
            '[ "$final_integrity_output" != "ok" ]; then'
        ),
        (
            "    printf 'OpenCode host-history integrity check failed after journal change: "
            "database=%s diagnostic=%s\\n' \"$history_db\" "
            '"$final_integrity_output" >&2'
        ),
        "    exit 1",
        "  fi",
        "fi",
    ]
