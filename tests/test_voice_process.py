"""The single subprocess boundary: allowlist, arguments, environment, limits."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

from primee.core.errors import ErrorCode, VoiceError
from primee.voice.process import ProcessSpec, SafeProcessRunner

from .voice_support import VoiceCase, interpreter_runner, real_interpreter


def spec(*arguments: str, **kwargs) -> ProcessSpec:
    return ProcessSpec(executable=real_interpreter(), arguments=tuple(arguments), **kwargs)


class ExecutableAllowlistTests(VoiceCase):
    def test_needs_at_least_one_absolute_root(self):
        with self.assertRaises(VoiceError):
            SafeProcessRunner([])
        with self.assertRaises(VoiceError):
            SafeProcessRunner([Path("relative/root")])

    def test_rejects_a_relative_executable_path(self):
        with self.assertRaises(VoiceError) as caught:
            interpreter_runner().validate_executable(Path("python3"))
        self.assertEqual(caught.exception.code, ErrorCode.VOICE_EXECUTABLE_REJECTED)

    def test_rejects_an_executable_outside_every_root(self):
        runner = SafeProcessRunner([self.tmp_path])
        with self.assertRaises(VoiceError) as caught:
            runner.validate_executable(real_interpreter())
        self.assertEqual(caught.exception.code, ErrorCode.VOICE_EXECUTABLE_REJECTED)

    def test_reports_a_missing_executable_inside_the_root(self):
        runner = SafeProcessRunner([self.tmp_path])
        with self.assertRaises(VoiceError) as caught:
            runner.validate_executable(self.tmp_path / "Scripts" / "python.exe")
        self.assertEqual(caught.exception.code, ErrorCode.VOICE_RUNTIME_MISSING)

    def test_rejects_traversal_out_of_the_root(self):
        runner = SafeProcessRunner([self.tmp_path])
        with self.assertRaises(VoiceError):
            runner.validate_executable(self.tmp_path / ".." / real_interpreter().name)

    def test_rejects_a_symlink_inside_the_root(self):
        link = self.tmp_path / "python-link"
        try:
            link.symlink_to(real_interpreter())
        except (OSError, NotImplementedError):
            self.skipTest("this platform or account cannot create symbolic links")
        runner = SafeProcessRunner([self.tmp_path])
        with self.assertRaises(VoiceError) as caught:
            runner.validate_executable(link)
        self.assertEqual(caught.exception.code, ErrorCode.VOICE_EXECUTABLE_REJECTED)

    def test_rejects_control_characters_in_the_path(self):
        with self.assertRaises(VoiceError):
            interpreter_runner().validate_executable(Path(str(real_interpreter()) + "\n"))

    def test_accepts_the_real_interpreter_under_its_own_directory(self):
        resolved = interpreter_runner().validate_executable(real_interpreter())
        self.assertTrue(resolved.is_file())


class ArgumentAndEnvironmentTests(VoiceCase):
    def test_arguments_must_be_strings_without_line_breaks(self):
        with self.assertRaises(VoiceError):
            SafeProcessRunner.validate_arguments(["ok", "bad\nline"])
        with self.assertRaises(VoiceError):
            SafeProcessRunner.validate_arguments(["ok", 3])  # type: ignore[list-item]
        with self.assertRaises(VoiceError):
            SafeProcessRunner.validate_arguments(["x"] * 65)

    def test_environment_is_sanitised_and_never_carries_path_or_proxies(self):
        os.environ["PRIMEE_TEST_LEAK"] = "leak"
        os.environ["HTTPS_PROXY_TEST_SENTINEL"] = "leak"
        try:
            env = SafeProcessRunner.build_environment()
        finally:
            del os.environ["PRIMEE_TEST_LEAK"]
            del os.environ["HTTPS_PROXY_TEST_SENTINEL"]
        self.assertNotIn("PATH", env)
        self.assertNotIn("PYTHONPATH", env)
        self.assertNotIn("PRIMEE_TEST_LEAK", env)
        self.assertNotIn("HTTPS_PROXY", env)
        self.assertNotIn("HTTP_PROXY", env)
        self.assertEqual(env["PYTHONNOUSERSITE"], "1")
        self.assertEqual(env["PYTHONUTF8"], "1")

    def test_extra_environment_cannot_set_path_like_variables(self):
        for name in ("PATH", "PYTHONPATH", "PYTHONHOME", "COMSPEC"):
            with self.assertRaises(VoiceError):
                SafeProcessRunner.build_environment({name: "x"})
        with self.assertRaises(VoiceError):
            SafeProcessRunner.build_environment({"lower": "x"})

    def test_the_child_really_sees_only_the_sanitised_environment(self):
        code = "import json,os,sys; print(json.dumps({'env': sorted(os.environ), 'isolated': sys.flags.isolated}))"
        outcome = interpreter_runner().run(spec("-I", "-c", code, timeout_seconds=30))
        self.assertTrue(outcome.succeeded, outcome.stderr)
        payload = json.loads(outcome.stdout)
        self.assertNotIn("PATH", payload["env"])
        self.assertNotIn("HTTPS_PROXY", payload["env"])
        self.assertNotIn("HTTP_PROXY", payload["env"])
        self.assertEqual(payload["isolated"], 1)


class ExecutionTests(VoiceCase):
    def test_stdin_text_reaches_the_child_and_nothing_is_added_to_arguments(self):
        code = "import sys; data=sys.stdin.buffer.read().decode('utf-8'); print(len(sys.argv)); print(data)"
        outcome = interpreter_runner().run(spec("-I", "-c", code, stdin_text="سلام پرایمی", timeout_seconds=30))
        self.assertTrue(outcome.succeeded, outcome.stderr)
        argc, text = outcome.stdout.strip().splitlines()
        self.assertEqual(argc, "1")  # only '-c'; the text itself is never an argument
        self.assertEqual(text, "سلام پرایمی")

    def test_non_zero_exit_is_reported_not_raised(self):
        outcome = interpreter_runner().run(spec("-I", "-c", "import sys; sys.stderr.write('boom\\n'); sys.exit(7)", timeout_seconds=30))
        self.assertFalse(outcome.succeeded)
        self.assertEqual(outcome.returncode, 7)
        self.assertIn("boom", outcome.stderr)

    def test_timeout_kills_the_child(self):
        outcome = interpreter_runner().run(spec("-I", "-c", "import time; time.sleep(30)", timeout_seconds=1))
        self.assertTrue(outcome.timed_out)
        self.assertIsNone(outcome.returncode)
        self.assertLess(outcome.duration_seconds, 20)

    def test_stdout_and_stderr_are_bounded(self):
        code = "import sys; sys.stdout.write('o'*300000); sys.stderr.write('e'*300000)"
        outcome = interpreter_runner().run(spec("-I", "-c", code, timeout_seconds=30, max_output_bytes=4096))
        self.assertTrue(outcome.succeeded)
        self.assertEqual(len(outcome.stdout), 4096)
        self.assertEqual(len(outcome.stderr), 4096)
        self.assertTrue(outcome.stdout_truncated)
        self.assertTrue(outcome.stderr_truncated)

    def test_output_limit_and_timeout_have_bounds(self):
        with self.assertRaises(VoiceError):
            interpreter_runner().run(spec("-c", "pass", max_output_bytes=10))
        with self.assertRaises(VoiceError):
            interpreter_runner().run(spec("-c", "pass", timeout_seconds=0))
        with self.assertRaises(VoiceError):
            interpreter_runner().run(spec("-c", "pass", timeout_seconds=6000))

    def test_too_much_stdin_is_refused_before_starting(self):
        with self.assertRaises(VoiceError) as caught:
            interpreter_runner().run(spec("-c", "pass", stdin_text="x" * 70000))
        self.assertEqual(caught.exception.code, ErrorCode.VOICE_TEXT_REJECTED)

    def test_working_directory_must_exist(self):
        with self.assertRaises(VoiceError):
            interpreter_runner().run(spec("-c", "pass", cwd=self.tmp_path / "nope"))

    def test_describe_carries_metadata_only(self):
        outcome = interpreter_runner().run(spec("-I", "-c", "print('secret words')", timeout_seconds=30))
        described = json.dumps(outcome.describe())
        self.assertNotIn("secret words", described)
        self.assertIn("stdout_bytes", described)


if __name__ == "__main__":
    unittest.main()
