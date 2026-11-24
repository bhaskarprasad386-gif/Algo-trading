#!/usr/bin/env python3
"""Redact broker credentials from journal output before writing GitHub Actions logs."""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.core.credential_redaction import redact_sensitive_text  # noqa: E402


def main() -> int:
    for line in sys.stdin:
        sys.stdout.write(redact_sensitive_text(line))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
