"""Helpers for the Primee Memory tests. Synthetic data only."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from primee.core.clock import FixedClock
from primee.memory.initializer import initialize_vault
from primee.memory.service import MemoryService
from primee.skills.vault.storage import VaultStorage

from .support import FIXED_NOW, fixed_clock

TODAY = "2026-08-27"


class MemoryCase(unittest.TestCase):
    """A temporary, initialised Vault plus a MemoryService bound to it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="primee-memory-")
        self.tmp_path = Path(self._tmp.name)
        self.vault_root = self.tmp_path / "TestVault"
        self.outside = self.tmp_path / "outside"
        self.outside.mkdir()
        self.clock = fixed_clock()
        initialize_vault(self.vault_root, self.clock)
        self.storage = VaultStorage(self.vault_root)
        self.memory = MemoryService(self.storage, self.clock)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- convenience builders -------------------------------------------
    def make_raw(self, title="Sample capture", body="Synthetic body.", **kwargs):
        kwargs.setdefault("summary", "A synthetic capture.")
        return self.memory.create_raw(title=title, body=body, actor="tester", **kwargs)

    def make_wiki(self, title="Sample topic", body="Synthetic knowledge.", **kwargs):
        kwargs.setdefault("summary", "A synthetic topic.")
        return self.memory.write_wiki(title=title, body=body, actor="tester", **kwargs)

    def make_output(self, title="Sample output", body="Synthetic output.", **kwargs):
        kwargs.setdefault("summary", "A synthetic output.")
        return self.memory.publish_output(title=title, body=body, actor="tester", **kwargs)

    def read_raw_file(self, relative: str) -> str:
        return (self.vault_root / relative).read_text(encoding="utf-8")

    def vault_files(self) -> list[str]:
        return sorted(
            p.relative_to(self.vault_root).as_posix()
            for p in self.vault_root.rglob("*")
            if p.is_file()
        )
