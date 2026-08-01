from __future__ import annotations

import argparse
import re
from pathlib import Path

EXPECTED_VERSION = b"1.18.10"
CLASSIFIER = re.compile(
    rb"if\(!(?P<error>[A-Za-z_$]+)\.data\.isRetryable&&!\("
    rb"(?P<status>[A-Za-z_$]+)!==void 0&&(?P=status)>=500\)\)return;"
)
POLICY = re.compile(
    rb"function (?P<name>[A-Za-z_$]+)\((?P<opts>[A-Za-z_$]+)\)\{return "
    rb"(?P<schedule>[A-Za-z_$]+)\.fromStepWithMetadata.*?\}\)\)\}"
)


def patch_bytes(binary: bytes, *, max_retries: int) -> bytes:
    if EXPECTED_VERSION not in binary:
        raise RuntimeError("unsupported OpenCode version; expected 1.18.10")
    if not isinstance(max_retries, int) or isinstance(max_retries, bool) or not 0 <= max_retries <= 9:
        raise ValueError("max_retries must be an integer between 0 and 9")

    classifier_matches = list(CLASSIFIER.finditer(binary))
    policy_matches = list(POLICY.finditer(binary))
    if len(classifier_matches) != 1 or len(policy_matches) != 1:
        raise RuntimeError("unsupported OpenCode compiled-code signature")

    classifier = classifier_matches[0]
    error = classifier.group("error")
    status = classifier.group("status")
    replacement = (
        b"if("
        + status
        + b"<500&&"
        + status
        + b"!=429||!"
        + error
        + b".data.isRetryable&&!"
        + status
        + b")return;"
    )
    binary = _fixed_width_replace(binary, classifier.span(), replacement)

    policy_matches = list(POLICY.finditer(binary))
    if len(policy_matches) != 1:
        raise RuntimeError("OpenCode retry policy signature changed during patching")
    policy = policy_matches[0]
    old = policy.group(0)
    attempt_match = re.search(
        rb"if\(!(?P<retry>[A-Za-z_$]+)\)return (?P<cause>[A-Za-z_$]+)\.done\("
        rb"(?P<meta>[A-Za-z_$]+)\.attempt\);",
        old,
    )
    if attempt_match is None:
        raise RuntimeError("unsupported OpenCode retry ceiling signature")
    retry = attempt_match.group("retry")
    cause = attempt_match.group("cause")
    meta = attempt_match.group("meta")
    old_condition = attempt_match.group(0)
    new_condition = (
        b"if(!"
        + retry
        + b"||"
        + meta
        + b".attempt>"
        + str(max_retries).encode()
        + b")return "
        + cause
        + b".done("
        + meta
        + b".attempt);"
    )
    patched_policy = old.replace(old_condition, new_condition, 1)
    patched_policy = re.sub(rb"action:[A-Za-z_$]+\.action,", b"", patched_policy, count=1)
    if len(patched_policy) > len(old):
        raise RuntimeError("patched OpenCode retry policy exceeds guarded binary slot")
    binary = _fixed_width_replace(binary, policy.span(), patched_policy)
    return binary


def _fixed_width_replace(binary: bytes, span: tuple[int, int], replacement: bytes) -> bytes:
    start, end = span
    width = end - start
    if len(replacement) > width:
        raise RuntimeError("patched OpenCode signature exceeds guarded binary slot")
    return binary[:start] + replacement.ljust(width, b" ") + binary[end:]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--max-retries", type=int, required=True)
    args = parser.parse_args()
    original = args.binary.read_bytes()
    patched = patch_bytes(original, max_retries=args.max_retries)
    args.binary.write_bytes(patched)
    print(
        "OPENCODE_RETRY_PATCH installed: version=1.18.10 "
        f"max_retries={args.max_retries} permanent_4xx=fail transient=bounded"
    )


if __name__ == "__main__":
    main()
