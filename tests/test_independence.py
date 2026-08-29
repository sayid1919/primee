"""Primee must run with no hosted AI service and no third-party dependency."""

from __future__ import annotations

import ast
import io
import json
import re
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from . import REPO_ROOT, SRC

FORBIDDEN_MODULES = {
    "anthropic",
    "openai",
    "google",
    "google_generativeai",
    "generativeai",
    "vertexai",
    "cohere",
    "mistralai",
    "ollama",
    "langchain",
    "langchain_openai",
    "llama_index",
    "transformers",
    "torch",
    "claude_code_sdk",
    "claude_agent_sdk",
    "requests",
    "httpx",
    "aiohttp",
    "urllib3",
    "yaml",
    "pydantic",
    "pytest",
}

NETWORK_MODULES = {"socket", "http", "urllib", "ftplib", "smtplib", "imaplib", "poplib", "telnetlib", "ssl", "asyncio"}

STDLIB_ALLOWED = set(sys.stdlib_module_names)


def source_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


class NoHostedAiTests(unittest.TestCase):
    def test_no_source_file_imports_an_ai_sdk_or_third_party_package(self):
        for path in source_files():
            roots = imported_roots(path)
            forbidden = roots & FORBIDDEN_MODULES
            self.assertEqual(forbidden, set(), msg=f"{path} imports {forbidden}")

    def test_every_import_is_stdlib_or_primee_itself(self):
        for path in source_files():
            for root in imported_roots(path):
                self.assertTrue(
                    root in STDLIB_ALLOWED or root == "primee" or root == "__future__",
                    msg=f"{path} imports non-stdlib module {root!r}",
                )

    def test_no_source_file_opens_a_network_connection(self):
        for path in source_files():
            roots = imported_roots(path)
            network = roots & NETWORK_MODULES
            self.assertEqual(network, set(), msg=f"{path} imports {network}")

    def test_no_source_file_mentions_a_hosted_ai_endpoint(self):
        pattern = re.compile(
            r"(?i)(api\.anthropic\.com|api\.openai\.com|generativelanguage\.googleapis|"
            r"api\.cohere|api\.mistral)"
        )
        for path in source_files():
            self.assertIsNone(pattern.search(path.read_text(encoding="utf-8")), msg=str(path))

    def test_pyproject_declares_no_runtime_dependencies(self):
        text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("dependencies = []", text)

    def test_no_claude_specific_directory_exists_in_the_repository(self):
        for name in (".claude", "claude", ".anthropic"):
            self.assertFalse((REPO_ROOT / name).exists(), msg=name)

    def test_no_source_file_executes_a_shell_command(self):
        # Bare builtins that turn data into code, plus the OS-command helpers.
        forbidden_names = {"eval", "exec", "compile", "__import__"}
        forbidden_attributes = {"system", "popen", "spawn", "spawnl", "execv", "execve"}
        forbidden_roots = {"subprocess", "shlex", "pty", "ctypes"}
        for path in source_files():
            self.assertEqual(imported_roots(path) & forbidden_roots, set(), msg=str(path))
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name):
                    self.assertNotIn(
                        node.func.id, forbidden_names, msg=f"{path} calls {node.func.id}()"
                    )
                elif isinstance(node.func, ast.Attribute):
                    self.assertNotIn(
                        node.func.attr,
                        forbidden_attributes,
                        msg=f"{path} calls .{node.func.attr}()",
                    )


class RepositoryHygieneTests(unittest.TestCase):
    """Guards against .gitignore accidentally excluding Primee's own source.

    An unanchored ``vault/`` rule matches ``src/primee/skills/vault/`` as well as
    a real Vault at the repository root, which would silently drop the Vault
    skill from every commit.
    """

    def test_no_source_test_config_or_doc_file_is_git_ignored(self):
        import subprocess  # noqa: PLC0415 - test-only, never imported by src/

        tracked_dirs = ["src", "tests", "config", "docs"]
        paths = [
            str(path.relative_to(REPO_ROOT))
            for directory in tracked_dirs
            for path in sorted((REPO_ROOT / directory).rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts
        ]
        paths += ["README.md", "pyproject.toml", "run_primee.py"]
        try:
            result = subprocess.run(
                ["git", "check-ignore", "--stdin"],
                cwd=REPO_ROOT,
                input="\n".join(paths),
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self.skipTest(f"git is not available here: {type(exc).__name__}")
        ignored = [line for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(ignored, [], msg=f".gitignore excludes Primee source: {ignored}")


class RunsLocallyTests(unittest.TestCase):
    def test_the_cli_runs_from_a_plain_checkout(self):
        from primee.cli import main

        config = str(REPO_ROOT / "config")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--config", config, "list-skills", "--json"]), 0)
            self.assertEqual(main(["list-skills", "--config", config, "--json"]), 0)

    def test_doctor_reports_no_hosted_ai_dependency(self):
        from primee.cli import main

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(["--config", str(REPO_ROOT / "config"), "doctor", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(buffer.getvalue())["hosted_ai_dependencies"], [])


if __name__ == "__main__":
    unittest.main()
