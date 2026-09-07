"""Nothing heavy, private or third-party may enter Git through the voice step."""

from __future__ import annotations

import subprocess  # noqa: PLC0415 - test-only, never imported by src/
import unittest

from . import REPO_ROOT

BINARY_SUFFIXES = (".onnx", ".wav", ".whl", ".dll", ".exe", ".bin", ".gguf", ".mp3", ".ogg", ".flac")


def git(*arguments: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *arguments], cwd=REPO_ROOT, input=stdin, capture_output=True, text=True, timeout=30
    )


class GitHygieneTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            probe = git("rev-parse", "--is-inside-work-tree")
        except (OSError, subprocess.SubprocessError) as exc:
            self.skipTest(f"git is not available here: {type(exc).__name__}")
        if probe.returncode != 0:
            self.skipTest("not a git work tree")

    def test_no_model_or_audio_file_is_tracked(self):
        tracked = git("ls-files").stdout.splitlines()
        offenders = [path for path in tracked if path.lower().endswith(BINARY_SUFFIXES)]
        self.assertEqual(offenders, [])

    def test_runtime_models_and_benchmarks_are_ignored(self):
        candidates = [
            ".venv-voice/Scripts/python.exe",
            ".venv-voice/Lib/site-packages/sherpa_onnx/__init__.py",
            "models/vits-mimic3-fa-haaniye_low/fa-haaniye_low.onnx",
            "models/vits-mimic3-fa-haaniye_low/fa-haaniye_low.onnx.json",
            "benchmarks/01-greeting.wav",
            "haaniye-benchmark-20260902/benchmark.json",
            "config/voice.local.toml",
            "anything/haaniye-sherpa.manifest.json",
        ]
        result = git("check-ignore", "--stdin", stdin="\n".join(candidates))
        ignored = set(result.stdout.splitlines())
        self.assertEqual(ignored, set(candidates))

    def test_voice_sources_tests_tools_and_examples_are_not_ignored(self):
        candidates = [
            "src/primee/voice/process.py",
            "src/primee/voice/adapters/sherpa.py",
            "tools/voice/sherpa_tts_worker.py",
            "tools/voice/Get-PrimeeVoiceManifest.ps1",
            "tools/voice/Install-PrimeeVoice.ps1",
            "config/voice.example.toml",
            "tests/fixtures/voice/haaniye-sherpa.manifest.json",
            "tests/test_voice_process.py",
        ]
        result = git("check-ignore", "--stdin", stdin="\n".join(candidates))
        self.assertEqual(result.stdout.strip(), "", msg=f"ignored: {result.stdout}")

    def test_the_repository_carries_no_manifest_generated_for_a_real_machine(self):
        tracked = git("ls-files").stdout.splitlines()
        manifests = [p for p in tracked if p.endswith(".manifest.json")]
        self.assertLessEqual(set(manifests), {"tests/fixtures/voice/haaniye-sherpa.manifest.json"})


class WindowsToolTests(unittest.TestCase):
    """Static checks on the PowerShell tools. They cannot run here; they can be read."""

    def read(self, name: str) -> str:
        return (REPO_ROOT / "tools" / "voice" / name).read_text(encoding="utf-8")

    def test_manifest_tool_downloads_nothing_and_pins_the_reviewed_hashes(self):
        text = self.read("Get-PrimeeVoiceManifest.ps1")
        self.assertIn("7b3bef2a558f57c403bb7afb994e081cd242e5df3384295b6738b198cb2f8382", text)
        self.assertIn("cbcb78ef8a3bb74acf0b9d0a8715e9b857491be8e2b1e8f589f09ecf3d2f2a0b", text)
        self.assertIn("$SherpaVersion = '1.13.7'", text)
        for forbidden in ("Invoke-Expression", "Start-BitsTransfer", "Set-ExecutionPolicy", "schtasks", "New-ScheduledTask", "[Environment]::SetEnvironmentVariable", "files.pythonhosted.org/packages"):
            self.assertNotIn(forbidden, text, msg=forbidden)
        self.assertIn("TryCreate", text)
        self.assertIn("publisher-git-blob-sha1", text)

    def test_installer_never_weakens_the_machine(self):
        text = self.read("Install-PrimeeVoice.ps1")
        for forbidden in ("Invoke-Expression", "Set-ExecutionPolicy", "schtasks", "New-ScheduledTask", "[Environment]::SetEnvironmentVariable", "CurrentVersion\\Run", "& git", "git checkout", "git reset", "git clean", "git.exe", "pip install --upgrade", "--pre", "cmd.exe", "ArgumentList"):
            self.assertNotIn(forbidden, text, msg=forbidden)
        for required in ("--require-hashes", "--no-index", "--no-deps", "-DryRun", "-Approve", "-Rollback", ".primee-installed.json", "PROVENANCE"):
            self.assertIn(required, text, msg=required)


if __name__ == "__main__":
    unittest.main()
