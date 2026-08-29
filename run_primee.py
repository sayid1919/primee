#!/usr/bin/env python3
"""Run Primee from a source checkout without installing anything.

    python run_primee.py doctor
    python run_primee.py run "morning brief"

This exists so Primee works on a bare Python installation with no packaging
step. ``pip install -e .`` and ``python -m primee`` work equally well.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from primee.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
