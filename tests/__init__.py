"""Primee test suite.

Every test here uses synthetic data only.  No test contacts a real account, a
real mailbox, a real calendar, a real website or the network, and no test reads
a credential.

Importing this package puts ``src/`` on ``sys.path`` so the suite runs from a
plain checkout with ``python -m unittest discover -s tests -t .`` and no install
step.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
