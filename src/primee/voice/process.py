"""The single subprocess boundary of Primee.

This is the ONLY module under ``src/`` allowed to import :mod:`subprocess`, and
``tests/test_independence.py`` enforces that. Everything it starts is:

* an executable given as an absolute path that Primee resolved itself from
  configuration, never from a request or a skill;
* inside an allowlisted directory (the isolated voice runtime), reached without
  following a symbolic link or junction;
* started with ``shell=False`` and an argument *list*, never a command string;
* given a sanitised environment, a timeout, and bounded capture of both output
  streams, so a misbehaving child cannot fill memory or hang Primee.

No command interpreter of any kind, no ``PATH`` lookup, no user-controlled executable.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from ..core.errors import ErrorCode, VoiceError

#: Environment variables passed through to a child. Nothing else survives.
#: These are what a Windows process needs to load its own DLLs and find a
#: temporary directory; no proxy, no PATH, no PYTHON* variables from outside.
ENV_PASSTHROUGH = ("SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL")

DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024
MAX_ARGUMENTS = 64
MAX_ARGUMENT_LENGTH = 4096
MAX_STDIN_BYTES = 64 * 1024


@dataclass(frozen=True)
class ProcessSpec:
    executable: Path
    arguments: tuple[str, ...]
    stdin_text: Optional[str] = None
    timeout_seconds: float = 60.0
    cwd: Optional[Path] = None
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    extra_env: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ProcessOutcome:
    returncode: Optional[int]
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def describe(self) -> dict:
        """Metadata only. Output content stays with the caller."""
        return {
            "returncode": self.returncode,
            "duration_seconds": round(self.duration_seconds, 3),
            "timed_out": self.timed_out,
            "stdout_bytes": len(self.stdout.encode("utf-8")),
            "stderr_bytes": len(self.stderr.encode("utf-8")),
            "stdout_truncated": self.stdout_truncated,
            "stderr_truncated": self.stderr_truncated,
        }


class SafeProcessRunner:
    """Runs one allowlisted executable with an argument list, and nothing else."""

    def __init__(self, allowed_roots: Sequence[Path]) -> None:
        roots = []
        for root in allowed_roots:
            path = Path(root)
            if not path.is_absolute():
                raise VoiceError(
                    ErrorCode.VOICE_EXECUTABLE_REJECTED,
                    "Every allowed executable root must be an absolute path.",
                )
            roots.append(path)
        if not roots:
            raise VoiceError(
                ErrorCode.VOICE_EXECUTABLE_REJECTED, "No executable root is allowlisted."
            )
        self._roots = tuple(roots)

    @property
    def allowed_roots(self) -> tuple[Path, ...]:
        return self._roots

    # -- validation -----------------------------------------------------
    def validate_executable(self, executable: Path) -> Path:
        """Prove the executable is a real file inside an allowlisted root."""
        if not isinstance(executable, Path):
            raise VoiceError(ErrorCode.VOICE_EXECUTABLE_REJECTED, "Executable must be a path.")
        if not executable.is_absolute():
            raise VoiceError(
                ErrorCode.VOICE_EXECUTABLE_REJECTED, "Executable path must be absolute."
            )
        text = str(executable)
        if "\x00" in text or any(ord(ch) < 32 for ch in text):
            raise VoiceError(
                ErrorCode.VOICE_EXECUTABLE_REJECTED, "Executable path contains a control character."
            )

        inside = None
        existing_roots = 0
        for root in self._roots:
            try:
                root_real = root.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if not root_real.is_dir():
                continue
            existing_roots += 1
            try:
                relative = executable.relative_to(root)
            except ValueError:
                try:
                    relative = executable.resolve().relative_to(root_real)
                except (ValueError, OSError, RuntimeError):
                    continue
            # Walk the path component by component so a symlink or junction
            # anywhere below the root is refused, not silently followed.
            current = root_real
            for part in relative.parts:
                if part in ("", ".", ".."):
                    raise VoiceError(
                        ErrorCode.VOICE_EXECUTABLE_REJECTED,
                        "Executable path must not contain '.' or '..' segments.",
                    )
                current = current / part
                if current.is_symlink():
                    raise VoiceError(
                        ErrorCode.VOICE_EXECUTABLE_REJECTED,
                        "Executable path passes through a symbolic link or junction.",
                    )
            resolved = Path(os.path.realpath(current))
            try:
                common = os.path.commonpath([str(root_real), str(resolved)])
            except ValueError:
                continue
            if os.path.normcase(common) == os.path.normcase(str(root_real)):
                inside = resolved
                break

        if inside is None:
            if existing_roots == 0:
                raise VoiceError(
                    ErrorCode.VOICE_RUNTIME_MISSING,
                    "The voice runtime directory does not exist; run the installer.",
                )
            raise VoiceError(
                ErrorCode.VOICE_EXECUTABLE_REJECTED,
                "Executable is outside every allowlisted voice runtime directory.",
            )
        if not inside.is_file():
            raise VoiceError(
                ErrorCode.VOICE_RUNTIME_MISSING, "Executable does not exist or is not a file."
            )
        if sys.platform != "win32" and not os.access(inside, os.X_OK):
            raise VoiceError(ErrorCode.VOICE_EXECUTABLE_REJECTED, "Executable is not executable.")
        return inside

    @staticmethod
    def validate_arguments(arguments: Sequence[str]) -> tuple[str, ...]:
        if len(arguments) > MAX_ARGUMENTS:
            raise VoiceError(ErrorCode.VOICE_EXECUTABLE_REJECTED, "Too many process arguments.")
        clean: list[str] = []
        for argument in arguments:
            if not isinstance(argument, str):
                raise VoiceError(
                    ErrorCode.VOICE_EXECUTABLE_REJECTED, "Every process argument must be a string."
                )
            if len(argument) > MAX_ARGUMENT_LENGTH:
                raise VoiceError(ErrorCode.VOICE_EXECUTABLE_REJECTED, "A process argument is too long.")
            if "\x00" in argument or "\n" in argument or "\r" in argument:
                raise VoiceError(
                    ErrorCode.VOICE_EXECUTABLE_REJECTED,
                    "A process argument contains a NUL byte or a line break.",
                )
            clean.append(argument)
        return tuple(clean)

    @staticmethod
    def build_environment(extra: Optional[dict] = None) -> dict[str, str]:
        env: dict[str, str] = {}
        for name in ENV_PASSTHROUGH:
            value = os.environ.get(name)
            if value:
                env[name] = value
        # The child is an isolated interpreter: no user site-packages, no .pyc
        # writing next to the worker, UTF-8 everywhere so Persian text survives.
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        for key, value in (extra or {}).items():
            if not isinstance(key, str) or not key.isidentifier() or not key.isupper():
                raise VoiceError(
                    ErrorCode.VOICE_EXECUTABLE_REJECTED, "Extra environment names must be UPPER_CASE identifiers."
                )
            if key in ("PATH", "PYTHONPATH", "PYTHONHOME", "COMSPEC", "PATHEXT"):
                raise VoiceError(
                    ErrorCode.VOICE_EXECUTABLE_REJECTED, f"Environment variable {key} may not be set for a child."
                )
            env[key] = str(value)
        return env

    # -- execution ------------------------------------------------------
    def run(self, spec: ProcessSpec) -> ProcessOutcome:
        executable = self.validate_executable(spec.executable)
        arguments = self.validate_arguments(spec.arguments)
        if not (0 < float(spec.timeout_seconds) <= 600):
            raise VoiceError(ErrorCode.VOICE_EXECUTABLE_REJECTED, "Timeout must be between 0 and 600 seconds.")
        if spec.max_output_bytes < 1024 or spec.max_output_bytes > 8 * 1024 * 1024:
            raise VoiceError(ErrorCode.VOICE_EXECUTABLE_REJECTED, "Output limit must be between 1 KiB and 8 MiB.")

        stdin_bytes: Optional[bytes] = None
        if spec.stdin_text is not None:
            stdin_bytes = spec.stdin_text.encode("utf-8")
            if len(stdin_bytes) > MAX_STDIN_BYTES:
                raise VoiceError(ErrorCode.VOICE_TEXT_REJECTED, "Too much text for one speech request.")

        cwd = None
        if spec.cwd is not None:
            cwd = Path(spec.cwd)
            if not cwd.is_absolute() or not cwd.is_dir():
                raise VoiceError(ErrorCode.VOICE_EXECUTABLE_REJECTED, "Working directory must be an existing absolute path.")

        command = [str(executable), *arguments]
        env = self.build_environment(spec.extra_env)
        started = time.monotonic()
        try:
            child = subprocess.Popen(  # noqa: S603 - the whole point of this module
                command,
                shell=False,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                cwd=str(cwd) if cwd else None,
                close_fds=True,
            )
        except OSError as exc:
            raise VoiceError(
                ErrorCode.VOICE_PROCESS_FAILED,
                "The voice runtime could not be started.",
                detail={"error_type": type(exc).__name__},
            ) from exc

        out_reader = _BoundedReader(child.stdout, spec.max_output_bytes)
        err_reader = _BoundedReader(child.stderr, spec.max_output_bytes)
        out_reader.start()
        err_reader.start()

        timed_out = False
        try:
            if child.stdin is not None:
                try:
                    if stdin_bytes:
                        child.stdin.write(stdin_bytes)
                    child.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            try:
                child.wait(timeout=float(spec.timeout_seconds))
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate(child)
        finally:
            out_reader.join(timeout=5)
            err_reader.join(timeout=5)
            for stream in (child.stdout, child.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass

        duration = time.monotonic() - started
        return ProcessOutcome(
            returncode=None if timed_out else child.returncode,
            stdout=out_reader.text(),
            stderr=err_reader.text(),
            duration_seconds=duration,
            timed_out=timed_out,
            stdout_truncated=out_reader.truncated,
            stderr_truncated=err_reader.truncated,
        )


def _terminate(child: "subprocess.Popen[bytes]") -> None:
    try:
        child.kill()
    except OSError:
        pass
    try:
        child.wait(timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        pass


class _BoundedReader(threading.Thread):
    """Drains a pipe completely but keeps at most ``limit`` bytes of it.

    Draining prevents the child from blocking on a full pipe; keeping only a
    bounded prefix prevents the child from exhausting Primee's memory.
    """

    def __init__(self, stream, limit: int) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._limit = limit
        self._chunks: list[bytes] = []
        self._kept = 0
        self.truncated = False

    def run(self) -> None:
        if self._stream is None:
            return
        try:
            while True:
                chunk = self._stream.read(8192)
                if not chunk:
                    break
                if self._kept < self._limit:
                    room = self._limit - self._kept
                    piece = chunk[:room]
                    self._chunks.append(piece)
                    self._kept += len(piece)
                    if len(chunk) > room:
                        self.truncated = True
                else:
                    self.truncated = True
        except (OSError, ValueError):
            return

    def text(self) -> str:
        return b"".join(self._chunks).decode("utf-8", errors="replace")
