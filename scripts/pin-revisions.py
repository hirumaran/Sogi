#!/usr/bin/env python3
"""Pin the exact upstream revisions of the external checkouts.

Writes ``external/revisions.json`` mapping each checkout directory to its
current commit SHA. Sogi consumes external projects through published
packages/CLIs, but reproducible research (eval comparisons, provider parity)
needs to know exactly which upstream source informed an adapter.

Usage: python scripts/pin-revisions.py [--check]

``--check`` exits non-zero if ``external/revisions.json`` is missing or drifts
from the actual checkouts; the doctor uses the same comparison as a warning.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

EXTERNAL_DIR = Path(__file__).resolve().parent.parent / "external"
REVISIONS_FILE = EXTERNAL_DIR / "revisions.json"


def current_revision(checkout: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=checkout,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    sha = completed.stdout.strip()
    return sha if completed.returncode == 0 and sha else None


def collect() -> dict[str, str]:
    revisions: dict[str, str] = {}
    for checkout in sorted(EXTERNAL_DIR.iterdir()):
        if not checkout.is_dir():
            continue
        sha = current_revision(checkout)
        if sha:
            revisions[checkout.name] = sha
    return revisions


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify without rewriting")
    args = parser.parse_args()

    actual = collect()
    if args.check:
        try:
            pinned = json.loads(REVISIONS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            print("external/revisions.json is missing or invalid; run pin-revisions.py")
            return 1
        drift = {name for name, sha in actual.items() if pinned.get(name) != sha}
        missing = set(pinned) - set(actual)
        if drift or missing:
            print(f"Revision drift: changed={sorted(drift)} removed={sorted(missing)}")
            return 1
        print(f"All {len(actual)} pinned revisions match their checkouts.")
        return 0

    REVISIONS_FILE.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Pinned {len(actual)} revision(s) to {REVISIONS_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
