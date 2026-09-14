"""The one-click Windows launchers: static checks that stand in for a Windows run.

PowerShell cannot execute in the test environment, so these tests pin what
the launcher files must and must not contain, that they stay PowerShell 5.1
compatible, and that their order of operations (validate, summarise, confirm,
only then install) is what the documentation promises.
"""

from __future__ import annotations

import re
import unittest

from . import REPO_ROOT

TOOLS = REPO_ROOT / "tools" / "voice"
POWERSHELL_FILES = (
    "Get-PrimeeVoiceManifest.ps1",
    "Install-PrimeeVoice.ps1",
    "Start-PrimeeVoice.ps1",
    "Rollback-PrimeeVoice.ps1",
)
LAUNCHERS = ("START_PRIMEE_VOICE_WINDOWS.cmd", "ROLLBACK_PRIMEE_VOICE_WINDOWS.cmd")

#: Syntax or cmdlets that exist only in PowerShell 6/7 and would break 5.1.
PS7_ONLY = (
    "??", "?.", " -Parallel", "Join-String", "-AsHashtable", "$IsWindows", "$IsLinux", "$IsMacOS",
    "Test-Json", "Get-Error", "-SkipCertificateCheck", "ArgumentList.Add", "#requires -version 6",
    "#requires -version 7", "ConvertFrom-Json -Depth", "-FollowRelLink", "Remove-Alias", "pwsh",
    "[System.Management.Automation.SemanticVersion]", "Get-Clipboard -Raw",
)
#: Things none of the launcher tools may ever do.
NEVER = (
    "Invoke-Expression", "iex ", "Set-ExecutionPolicy", "schtasks", "New-ScheduledTask", "Register-ScheduledTask",
    "[Environment]::SetEnvironmentVariable", "CurrentVersion\\Run", "Start-BitsTransfer", "cmd.exe /c", "cmd /c",
    "& git", "git checkout", "git reset", "git clean", "git pull", "git.exe", "pip install --upgrade",
    "runas", "Start-Process -Verb RunAs", "-Verb RunAs", "Set-ItemProperty -Path HKCU", "HKLM:",
)


def read(name: str) -> str:
    return (TOOLS / name).read_text(encoding="utf-8")


def strip_comments(text: str) -> str:
    """Remove block comments and line comments so string checks hit code, not prose."""
    text = re.sub(r"<#.*?#>", "", text, flags=re.S)
    return "\n".join(line.split("#", 1)[0] if "#" in line and not line.lstrip().startswith("'") else line for line in text.splitlines())


class LauncherFileTests(unittest.TestCase):
    def test_both_launchers_exist_at_the_repository_root(self):
        for name in LAUNCHERS:
            self.assertTrue((REPO_ROOT / name).is_file(), name)

    def test_launchers_use_crlf_and_plain_ascii(self):
        for name in LAUNCHERS:
            raw = (REPO_ROOT / name).read_bytes()
            self.assertTrue(raw.isascii(), name)
            self.assertIn(b"\r\n", raw, name)
            self.assertNotIn(b"\n", raw.replace(b"\r\n", b""), msg=f"{name} has a bare LF")

    def test_launchers_start_the_right_script_without_a_command_string(self):
        for name, script in (("START_PRIMEE_VOICE_WINDOWS.cmd", "Start-PrimeeVoice.ps1"), ("ROLLBACK_PRIMEE_VOICE_WINDOWS.cmd", "Rollback-PrimeeVoice.ps1")):
            text = (REPO_ROOT / name).read_text(encoding="ascii")
            self.assertIn("%~dp0tools\\voice\\" + script, text)
            self.assertIn('"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"', text)
            self.assertIn("-NoProfile", text)
            self.assertIn("-ExecutionPolicy Bypass", text)
            self.assertIn('-File "%SCRIPT%"', text)
            self.assertIn("pause", text)
            self.assertIn("chcp 65001", text)
            self.assertNotIn("-Command", text)
            self.assertNotIn("-EncodedCommand", text)
            self.assertNotIn("-NonInteractive", text, msg="the person must be able to answer the confirmation")
            self.assertNotIn("runas", text.lower())
            self.assertTrue((TOOLS / script).is_file(), script)

    def test_gitattributes_keeps_crlf_for_cmd_files(self):
        text = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("*.cmd text eol=crlf", text)


class PowerShell51CompatibilityTests(unittest.TestCase):
    def test_no_powershell_7_only_constructs(self):
        for name in POWERSHELL_FILES:
            code = strip_comments(read(name))
            for construct in PS7_ONLY:
                self.assertNotIn(construct, code, msg=f"{name}: {construct}")

    def test_scripts_with_non_ascii_text_carry_a_utf8_bom(self):
        # Windows PowerShell 5.1 reads a BOM-less file as ANSI, which would corrupt Persian.
        for name in POWERSHELL_FILES:
            raw = (TOOLS / name).read_bytes()
            if not raw.isascii():
                self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), msg=f"{name} needs a UTF-8 BOM")

    def test_braces_and_parentheses_balance(self):
        for name in POWERSHELL_FILES:
            text = read(name)
            self.assertEqual(text.count("{"), text.count("}"), name)
            self.assertEqual(text.count("("), text.count(")"), name)

    def test_no_reserved_automatic_variable_is_assigned(self):
        pattern = re.compile(r"^\s*\$(host|profile|input|args|error|PSItem|matches|this|null|true|false)\s*=", re.I | re.M)
        for name in POWERSHELL_FILES:
            self.assertIsNone(pattern.search(strip_comments(read(name))), name)

    def test_strict_mode_and_stop_on_error_everywhere(self):
        for name in POWERSHELL_FILES:
            text = read(name)
            self.assertIn("Set-StrictMode -Version 2.0", text, name)
            self.assertIn("$ErrorActionPreference = 'Stop'", text, name)

    def test_inline_python_snippets_contain_no_double_quotes(self):
        # Windows PowerShell 5.1 strips embedded double quotes from arguments handed to a
        # native program, so python -c 'print("x")' arrives as print(x). Found on the first
        # real run; every inline snippet must be quote-free and pass values via sys.argv.
        pattern = re.compile(r"'-c',\s*'([^']*)'")
        for name in POWERSHELL_FILES:
            for match in pattern.finditer(read(name)):
                self.assertNotIn('"', match.group(1), msg=f"{name}: {match.group(1)}")

    def test_write_host_never_concatenates_outside_parentheses(self):
        # `Write-Host 'a' + $b` prints three arguments; the launcher must wrap concatenations.
        pattern = re.compile(r"^\s*Write-(Host|Output)\s+'[^']*'\s*\+", re.M)
        for name in POWERSHELL_FILES:
            self.assertIsNone(pattern.search(read(name)), name)


class LauncherSafetyTests(unittest.TestCase):
    def test_nothing_forbidden_anywhere(self):
        for name in POWERSHELL_FILES:
            code = strip_comments(read(name))
            for construct in NEVER:
                self.assertNotIn(construct, code, msg=f"{name}: {construct}")

    def test_child_scripts_are_started_by_explicit_path_with_argument_arrays(self):
        start = strip_comments(read("Start-PrimeeVoice.ps1"))
        self.assertIn("Join-Path $env:SystemRoot 'System32\\WindowsPowerShell\\v1.0\\powershell.exe'", start)
        self.assertIn("'-File', $manifestTool, '-OutputPath', $manifestPath, '-Force'", start)
        self.assertIn("'-File', $installer, '-Approve', '-ManifestPath', $manifestPath", start)
        self.assertIn("& $Exe @Arguments", start)

    def test_installer_parameters_used_by_the_launcher_exist(self):
        installer = read("Install-PrimeeVoice.ps1")
        manifest_tool = read("Get-PrimeeVoiceManifest.ps1")
        for parameter in ("$ManifestPath", "$ModelsPath", "$RuntimePath", "$PythonExe", "$Approve", "$Rollback"):
            self.assertIn(parameter, installer, parameter)
        for parameter in ("$OutputPath", "$Force"):
            self.assertIn(parameter, manifest_tool, parameter)

    def test_validation_and_confirmation_come_before_installation(self):
        start = strip_comments(read("Start-PrimeeVoice.ps1"))
        validate = start.index("Test-Manifest -Path $manifestPath")
        summary = start.index("خلاصه")
        nothing_yet = start.index("Nothing has been downloaded yet.")
        confirm = start.index("Read-Host")
        install = start.index("'-Approve', '-ManifestPath'")
        self.assertLess(validate, summary)
        self.assertLess(summary, nothing_yet)
        self.assertLess(nothing_yet, confirm)
        self.assertLess(confirm, install)
        self.assertIn(".ToUpper() -ne 'YES'", start)

    def test_manifest_validation_pins_the_reviewed_values(self):
        start = read("Start-PrimeeVoice.ps1")
        self.assertIn("$SherpaVersion = '1.13.7'", start)
        self.assertIn("7b3bef2a558f57c403bb7afb994e081cd242e5df3384295b6738b198cb2f8382", start)
        self.assertIn("cbcb78ef8a3bb74acf0b9d0a8715e9b857491be8e2b1e8f589f09ecf3d2f2a0b", start)
        self.assertIn("'^[0-9a-f]{40}$'", start)
        for required in ("'fa-haaniye_low.onnx'", "'fa-haaniye_low.onnx.json'", "'tokens.txt'", "'espeak-ng-data/phontab'", "'espeak-ng-data/phonindex'", "'espeak-ng-data/phondata'"):
            self.assertIn(required, start, required)
        self.assertIn("$MinEspeakFiles", start)
        self.assertIn("publisher-sha256", start)
        self.assertIn("files\\.pythonhosted\\.org", start)
        self.assertIn("huggingface\\.co/.+/resolve/", start)

    def test_licence_and_provenance_warnings_stay_visible(self):
        start = read("Start-PrimeeVoice.ps1")
        self.assertIn("CC-0", start)
        self.assertIn("SOURCE", start)
        self.assertIn("public domain", start)
        self.assertIn("جنسیت گوینده", start)
        self.assertIn("بازتوزیع", start)
        self.assertIn("Step Three is not complete", start)
        self.assertIn("گام سوم کامل نیست", start)

    def test_runtime_version_is_checked_after_installation(self):
        start = read("Start-PrimeeVoice.ps1")
        self.assertIn("print(version(sys.argv[1])); print(version(sys.argv[2]))', 'sherpa-onnx', 'sherpa-onnx-core'", start)
        self.assertIn("-ne $SherpaVersion", start)

    def test_test_and_benchmark_go_through_primee_itself(self):
        start = read("Start-PrimeeVoice.ps1")
        self.assertIn("'voice', 'speak', $TestSentence, '--approve', 'audio.playback'", start)
        self.assertIn("'voice', 'benchmark', '--output', $benchmarkDir", start)
        self.assertIn("'voice', 'status', '--json'", start)
        self.assertIn("'-X', 'utf8'", start)

    def test_logs_are_sanitised_and_never_contain_the_spoken_text(self):
        start = strip_comments(read("Start-PrimeeVoice.ps1"))
        self.assertIn("function Protect-Text", start)
        self.assertIn("'<user>'", start)
        self.assertIn("'%' + $name + '%'", start)
        # Every speak/status/benchmark call hides its raw JSON from the log.
        for call in ("'voice', 'speak'", "'voice', 'status'", "'voice', 'benchmark'", "'doctor'"):
            index = start.index(call)
            line_end = start.index("\n", index)
            self.assertIn("-HideOutput", start[index:line_end], call)
        self.assertIn("diagnostics", start)

    def test_launcher_never_modifies_repository_config_or_deletes_user_folders(self):
        start = strip_comments(read("Start-PrimeeVoice.ps1"))
        # The only file the launcher itself deletes is the temporary stderr capture it created.
        for match in re.finditer(r"Remove-Item[^\n]*", start):
            self.assertIn("$errFile", match.group(0), match.group(0))
        self.assertNotIn("voice.local.toml", start)
        self.assertNotIn("permissions.local.toml", start)
        self.assertIn("'launcher'", start)
        self.assertIn(".primee-launcher-created.json", start)

    def test_elevation_is_refused_and_windows_10_required(self):
        start = read("Start-PrimeeVoice.ps1")
        self.assertIn("function Test-Elevated", start)
        self.assertIn("if (Test-Elevated) { Stop-Launcher", start)
        self.assertIn("$osVersion.Major -lt 10", start)

    def test_rollback_only_removes_recorded_paths_with_confirmation(self):
        rollback = strip_comments(read("Rollback-PrimeeVoice.ps1"))
        confirm = rollback.index("Read-Host")
        remove = rollback.index("-Rollback -ModelsPath")
        self.assertLess(confirm, remove)
        self.assertIn(".primee-launcher-created.json", rollback)
        self.assertIn("refusing to remove a path outside the launcher folder", rollback)
        self.assertNotIn("Remove-Item -LiteralPath $ModelsPath", rollback)
        self.assertNotIn("Remove-Item -LiteralPath $repoRoot", rollback)


if __name__ == "__main__":
    unittest.main()
