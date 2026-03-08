"""
bulk_register_other.py - Bulk-register channels from a text file.

Each non-empty, non-comment line is treated as a channel URL/identifier and
registered through register_channel.py with --group other.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def iter_identifiers(file_path: Path) -> list[str]:
    lines = file_path.read_text(encoding="utf-8").splitlines()
    values: list[str] = []
    for line in lines:
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        values.append(value)
    return values


def run_add(identifier: str, group_name: str) -> int:
    cmd = [
        sys.executable,
        "register_channel.py",
        "add",
        identifier,
        "--group",
        group_name,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)

    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.stderr:
        print(proc.stderr.rstrip())

    return int(proc.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bulk-register channels from text file")
    parser.add_argument(
        "file",
        nargs="?",
        default="other_channels.txt",
        help="path to URL list text file (default: other_channels.txt)",
    )
    parser.add_argument(
        "--group",
        default="other",
        help="group name to force on registration (default: other)",
    )
    args = parser.parse_args()

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"[ERROR] file not found: {file_path}")
        return 2

    identifiers = iter_identifiers(file_path)
    if not identifiers:
        print("[INFO] no channel identifiers found.")
        return 0

    total = len(identifiers)
    success = 0
    failed = 0

    print(f"[INFO] start bulk registration: {total} entries")
    for idx, identifier in enumerate(identifiers, start=1):
        print(f"\n[{idx}/{total}] {identifier}")
        code = run_add(identifier, args.group)
        if code == 0:
            success += 1
        else:
            failed += 1
            print(f"[ERROR] registration failed (exit={code})")

    print("\n=== Summary ===")
    print(f"success: {success}")
    print(f"failed : {failed}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
