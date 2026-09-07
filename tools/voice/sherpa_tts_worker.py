#!/usr/bin/env python3
"""Primee speech worker: text in on stdin, one WAV file out, one JSON line back.

This file runs INSIDE the isolated voice runtime (``.venv-voice``), never in
Primee Core, and it is the only Primee code that imports sherpa-onnx. It is
started by ``primee.voice.process.SafeProcessRunner`` in Python isolated mode
(``-I``) with an explicit argument list.

Guarantees:

* reads the text from standard input only; no text ever appears in arguments,
  the environment, logs or files other than the WAV it was asked to write;
* opens no network connection (nothing here imports a networking module);
* writes exactly one file, the ``--output`` path, and refuses if it exists;
* prints exactly one JSON line to stdout on success, exits non-zero with a
  one-line JSON error on stderr otherwise.

Usage (as invoked by Primee, never by hand with real text):

    python -I -B -X utf8 sherpa_tts_worker.py --model M.onnx --tokens tokens.txt \
        --data-dir espeak-ng-data --output out.wav --speaker-id 0 --speed 1.00 \
        --max-chars 240 < text.txt
"""

from __future__ import annotations

import argparse
import array
import json
import os
import sys
import time
import wave
from pathlib import Path

RESULT_SCHEMA = "primee-tts-worker-result"
ERROR_SCHEMA = "primee-tts-worker-error"
ABSOLUTE_MAX_CHARS = 2000


def _fail(code: str, message: str, status: int = 2) -> "NoReturn":  # type: ignore[name-defined]
    sys.stderr.write(json.dumps({"schema": ERROR_SCHEMA, "code": code, "message": message}) + "\n")
    sys.stderr.flush()
    raise SystemExit(status)


def _existing_file(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or not path.is_file():
        raise argparse.ArgumentTypeError(f"not an existing absolute file: {path.name}")
    return path


def _existing_dir(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or not path.is_dir():
        raise argparse.ArgumentTypeError(f"not an existing absolute directory: {path.name}")
    return path


def _new_file(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("output must be an absolute path")
    if path.suffix.lower() != ".wav":
        raise argparse.ArgumentTypeError("output must end in .wav")
    if path.exists():
        raise argparse.ArgumentTypeError("output already exists; refusing to overwrite")
    if not path.parent.is_dir():
        raise argparse.ArgumentTypeError("output directory does not exist")
    return path


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="sherpa_tts_worker", add_help=False)
    parser.add_argument("--model", type=_existing_file, required=True)
    parser.add_argument("--tokens", type=_existing_file, required=True)
    parser.add_argument("--data-dir", type=_existing_dir, required=True)
    parser.add_argument("--output", type=_new_file, required=True)
    parser.add_argument("--speaker-id", type=int, default=0)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--max-chars", type=int, default=240)
    parser.add_argument("--threads", type=int, default=1)
    return parser.parse_args(argv)


def read_text(max_chars: int) -> str:
    raw = sys.stdin.buffer.read(ABSOLUTE_MAX_CHARS * 4 + 1)
    text = raw.decode("utf-8", errors="strict").strip()
    if not text:
        _fail("EMPTY_TEXT", "no text on stdin")
    if len(text) > max_chars or len(text) > ABSOLUTE_MAX_CHARS:
        _fail("TEXT_TOO_LONG", f"text longer than {min(max_chars, ABSOLUTE_MAX_CHARS)} characters")
    if "\x00" in text:
        _fail("TEXT_REJECTED", "text contains a NUL byte")
    return text


def peak_memory() -> tuple[int | None, str]:
    """Peak resident memory of this process, only where the standard library offers it."""
    try:
        import resource  # POSIX only

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports KiB, macOS reports bytes.
        kib = int(usage) if sys.platform != "darwin" else int(usage) // 1024
        return kib, "ru_maxrss of the worker process"
    except (ImportError, AttributeError, ValueError):
        return None, "not measurable with the standard library on this platform (Windows has no resource module)"


def write_wav(path: Path, samples, sample_rate: int) -> int:
    """16-bit PCM mono. Returns the number of frames written."""
    pcm = array.array("h")
    for value in samples:
        clipped = max(-1.0, min(1.0, float(value)))
        pcm.append(int(round(clipped * 32767.0)))
    if sys.byteorder != "little":
        pcm.byteswap()
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(int(sample_rate))
        handle.writeframes(pcm.tobytes())
    return len(pcm)


def main(argv: list[str]) -> int:
    try:
        args = parse_args(argv)
    except SystemExit:
        _fail("BAD_ARGUMENTS", "invalid arguments")
    if args.speaker_id < 0:
        _fail("BAD_ARGUMENTS", "speaker id must be non-negative")
    if not (0.5 <= args.speed <= 2.0):
        _fail("BAD_ARGUMENTS", "speed must be between 0.5 and 2.0")
    if not (1 <= args.threads <= 4):
        _fail("BAD_ARGUMENTS", "threads must be between 1 and 4")

    text = read_text(args.max_chars)

    try:
        import sherpa_onnx  # the only third-party import, inside the isolated runtime
    except ImportError:
        _fail("RUNTIME_MISSING", "sherpa_onnx is not installed in this interpreter", status=3)

    try:
        config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=str(args.model),
                    lexicon="",
                    tokens=str(args.tokens),
                    data_dir=str(args.data_dir),
                ),
                provider="cpu",
                debug=False,
                num_threads=args.threads,
            ),
            rule_fsts="",
            max_num_sentences=1,
        )
        if not config.validate():
            _fail("MODEL_CONFIG_INVALID", "sherpa-onnx rejected the model configuration", status=4)
        tts = sherpa_onnx.OfflineTts(config)
    except Exception as exc:  # noqa: BLE001 - report the class, never the text
        _fail("ENGINE_INIT_FAILED", type(exc).__name__, status=4)

    started = time.perf_counter()
    try:
        audio = tts.generate(text, sid=args.speaker_id, speed=args.speed)
    except Exception as exc:  # noqa: BLE001
        _fail("SYNTHESIS_FAILED", type(exc).__name__, status=5)
    synthesis_seconds = time.perf_counter() - started

    samples = audio.samples
    sample_rate = int(audio.sample_rate)
    if sample_rate <= 0 or len(samples) == 0:
        _fail("EMPTY_AUDIO", "the engine produced no samples", status=5)

    frames = write_wav(args.output, samples, sample_rate)
    output_bytes = os.path.getsize(args.output)
    peak_kb, peak_note = peak_memory()

    sys.stdout.write(
        json.dumps(
            {
                "schema": RESULT_SCHEMA,
                "engine": "sherpa-onnx",
                "engine_version": str(getattr(sherpa_onnx, "__version__", "unknown")),
                "sample_rate": sample_rate,
                "num_samples": frames,
                "audio_seconds": round(frames / sample_rate, 4),
                "synthesis_seconds": round(synthesis_seconds, 4),
                "output_bytes": output_bytes,
                "peak_memory_kb": peak_kb,
                "peak_memory_note": peak_note,
                "characters": len(text),
            },
            ensure_ascii=True,
        )
        + "\n"
    )
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
