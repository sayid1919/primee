# Primee Voice (Step Three) — text-to-speech benchmark preparation

**Status: preparation only.** Speech output exists as an optional, local-only
layer with a fixed benchmark. Push-to-talk, speech-to-text and the complete
microphone-to-speaker loop are **not built**. `primee voice` says so and does
nothing else. Step Three is not complete.

## What exists

```
Primee Core (standard library only)
  └─ primee.voice.service.VoiceService        decides: permission → config → engine
       ├─ primee.voice.process.SafeProcessRunner   the ONLY subprocess boundary in src/
       │     └─ <runtime>\Scripts\python.exe -I -B -X utf8 tools\voice\sherpa_tts_worker.py
       │           └─ sherpa_onnx.OfflineTts → Haaniye ONNX → one temporary WAV
       ├─ primee.voice.playback.WinsoundPlayer     standard-library playback, Windows only
       └─ primee.voice.adapters.text_only          the fallback that is always there
```

| Module | Role |
| --- | --- |
| `voice/interfaces.py` | `TextToSpeech`, `AudioPlayer`, `SpeechRequest`, `SpeechResult`, `Availability` — the engine-neutral contract |
| `voice/process.py` | `SafeProcessRunner`: absolute executable inside an allowlisted root, no symlinks, `shell=False`, argument list, sanitised environment, timeout, bounded stdout/stderr |
| `voice/manifest.py` | pinned manifest parsing; `publisher-sha256` and `publisher-git-blob-sha1` kept apart; byte-for-byte verification of installed files |
| `voice/profiles.py` | the Haaniye record: quoted licences, `SOURCE = TBD` warning, gender not stated |
| `voice/adapters/sherpa.py` | starts the worker through the runner; never imports sherpa-onnx |
| `voice/adapters/text_only.py` | text-only fallback |
| `voice/service.py` | `VoiceService.speak()` / `speak_result()` / `status()`; audits counts, never words |
| `voice/summarize.py` | the short spoken summary; `structured_data` is never spoken |
| `voice/tempaudio.py` | temporary WAV that is deleted on success and on failure |
| `voice/playback.py` | `winsound` player, `NullPlayer` |
| `voice/session.py` | transcript policy for the future STT step: memory only, explicit approval to persist |
| `voice/benchmark.py` | the fixed seven-phrase Persian benchmark |
| `tools/voice/sherpa_tts_worker.py` | runs **inside** the isolated runtime; the only file that imports sherpa-onnx |
| `tools/voice/Get-PrimeeVoiceManifest.ps1` | Windows: build the pinned manifest from public metadata; downloads nothing |
| `tools/voice/Install-PrimeeVoice.ps1` | Windows: `-DryRun` / `-Approve` / `-Rollback` from one approved manifest |

## What stays optional and external

Primee Core keeps zero runtime dependencies. `tests/test_independence.py`
still parses every file under `src/` and allows `subprocess` in exactly one
module, `primee/voice/process.py`, under additional rules (one `Popen`,
`shell=False`, list argument, no `PATH` lookup). The engine and the model are:

| Component | Where | Never |
| --- | --- | --- |
| `sherpa-onnx` + `sherpa-onnx-core` wheels (Apache-2.0) | `%LOCALAPPDATA%\Primee\voice-runtime\` (a `.venv-voice` inside the repository is also ignored by Git) | imported by Core, added to `pyproject.toml` |
| Haaniye model files + `espeak-ng-data` | `%LOCALAPPDATA%\Primee\models\vits-mimic3-fa-haaniye_low\` or `PRIMEE_MODELS_PATH` | inside the repository |
| Mycroft provenance files (`LICENSE`, `README.md`, `SOURCE`, `ALIASES`) + `PROVENANCE.json` | next to the model under `PROVENANCE\` | rewritten or re-licensed |
| `config\voice.local.toml` | git-ignored | committed |

## Decision order when speaking

1. `audio.playback` in the permission policy: `never` (default) → text only;
   `approval` → text only unless `--approve audio.playback`; `auto` → continue.
2. `[voice] enabled` and `tts_engine` → otherwise text only.
3. Adapter availability: runtime interpreter inside the allowlisted root, worker
   script present, model files present, manifest present **and verified**.
4. The text is shortened to a summary (`max_spoken_chars`, sentence boundary).
5. Synthesis into a private temporary WAV → playback → deletion, always.

Every fallback returns the text for the screen and writes one audit event with
counts and durations only. No transcript, no spoken text, no file path with a
user name reaches the audit log.

## Haaniye: licence and provenance, separately

| Subject | Licence | Source | Status |
| --- | --- | --- | --- |
| Voice (original) | **CC0** | `MycroftAI/mimic3-voices` `voices/fa/haaniye_low/LICENSE` = `CC-0` | read from the raw file |
| Dataset | not stated | README: "based on a public domain dataset" | described, not licensed |
| Provenance | — | `SOURCE` file = `TBD` | **incomplete** |
| Converted repository | none declared | `csukuangfj/vits-mimic3-fa-haaniye_low` | Primee assigns none |
| Engine | Apache-2.0 | `k2-fsa/sherpa-onnx` LICENSE, PyPI | read |
| Bundled libraries (onnxruntime, espeak-ng data, …) | their own | inside `sherpa-onnx-core` | **not assessed** |

Consequences, recorded in `profiles.py` and repeated by every result:

- approved for a **private local benchmark only**;
- **no** claim of redistribution or commercial clearance;
- speaker gender is **not stated** by the official documentation. The person
  chose Haaniye as the female-voice candidate; perceived gender is confirmed by
  listening, not by Primee.

## The pinned manifest

Manifests are generated **on the Windows computer**, from public metadata,
because the development container cannot reach Hugging Face. The tool writes one
JSON file and downloads nothing:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\voice\Get-PrimeeVoiceManifest.ps1
```

It re-fetches the sherpa-onnx 1.13.7 wheel metadata and **stops** if PyPI's
size, SHA-256 or dependency list differs from the values reviewed in the
session; it pins the model repository to its current commit and lists every
file (including all of `espeak-ng-data/`) with its size and publisher hash; it
pins `mimic3-voices` to its current commit and records the four provenance
files verbatim.

Hash kinds are never mixed:

- `publisher-sha256` — PyPI digests, Hugging Face LFS metadata (the `.onnx`);
- `publisher-git-blob-sha1` — Hugging Face tree `oid` for non-LFS files,
  recomputed locally as `sha1("blob <size>\0" + content)`;
- `computed-sha256-at-manifest-time` — provenance text files; GitHub publishes
  no per-file hash.

A matching hash is a content match against what the publisher indexed. It is
not a security guarantee and not an independent signature.

## One-click Windows setup

For a person who wants no terminal at all:

```
Download the branch ZIP → extract → double-click START_PRIMEE_VOICE_WINDOWS.cmd
→ read the Persian summary → type YES → hear the Haaniye test sentence
```

`START_PRIMEE_VOICE_WINDOWS.cmd` starts `tools\voice\Start-PrimeeVoice.ps1`
in Windows PowerShell 5.1 with a process-scoped execution policy (no system
setting is changed). The orchestrator, in order: checks Windows 10/11 and
refuses to run elevated; locates the repository next to the launcher; finds
Python 3.11 64-bit; proves Primee Core runs in text-only mode; checks free
disk space; generates the pinned manifest from public metadata (nothing
downloaded); validates it automatically (runtime 1.13.7, exact wheel names,
sizes and reviewed SHA-256 values, 40-character model revision, every required
model file including `espeak-ng-data/`, upstream `LICENSE` still `CC-0`);
shows a Persian summary with all licence and provenance warnings; asks for one
`YES`; only then calls `Install-PrimeeVoice.ps1 -Approve` with that manifest;
confirms the installed runtime version; runs `primee voice speak` for one
Persian test sentence (played through `winsound`, temporary WAV deleted by
Primee); writes the seven-phrase benchmark to `%LOCALAPPDATA%\Primee\benchmarks`
for listening. A sanitised diagnostic report (no account name, no home path,
no spoken text) is written to `%LOCALAPPDATA%\Primee\diagnostics` on every run.

The launcher never edits the repository's `config\*.local.toml` files: the test
and the benchmark use a launcher-owned configuration folder under
`%LOCALAPPDATA%\Primee\launcher`. It never deletes a pre-existing folder; if a
runtime or model folder exists that the installer did not create, it stops and
says so; if a *recorded* installation is incomplete (for example the model
without its runtime), it asks once and removes only the recorded items before
starting over. The runtime lives under `%LOCALAPPDATA%\Primee\voice-runtime`,
not in the extracted ZIP folder, so re-extracting a newer ZIP keeps the
installation. `ROLLBACK_PRIMEE_VOICE_WINDOWS.cmd` removes only what the installer and
the launcher recorded as created, after one confirmation, without any Git
command.

`tests/test_voice_launcher.py` pins these properties statically (order of
validate → summary → confirm → install, PowerShell 5.1 compatibility, UTF-8
BOM for the Persian text, no policy or PATH change, no elevation). The first
real Windows execution has not happened yet.

## Installing by hand — only after the manifest is approved

```powershell
# 1. read everything it would do; changes nothing
powershell -NoProfile -ExecutionPolicy Bypass -File tools\voice\Install-PrimeeVoice.ps1 -DryRun
# 2. exactly that, from exactly that manifest
powershell -NoProfile -ExecutionPolicy Bypass -File tools\voice\Install-PrimeeVoice.ps1 -Approve
# rollback: removes only what -Approve recorded as created
powershell -NoProfile -ExecutionPolicy Bypass -File tools\voice\Install-PrimeeVoice.ps1 -Rollback
```

The installer: needs no Administrator rights; downloads only from
`files.pythonhosted.org` and `huggingface.co` at the pinned revision; verifies
every file **before** it is used and stops on any mismatch; creates the venv
with `python -m venv` and installs with `pip --no-index --no-deps
--require-hashes` from the verified local wheels, so nothing is resolved to
"latest" and pip is not upgraded; never touches ExecutionPolicy, `PATH`,
scheduled tasks or startup; refuses to overwrite an existing runtime, model
folder or `voice.local.toml`; records what it created in
`.primee-installed.json` so `-Rollback` removes exactly that and nothing else,
without any git command. Changing the manifest means approving again.

## The benchmark

```powershell
python run_primee.py voice status
python run_primee.py voice benchmark --output "%LOCALAPPDATA%\Primee\benchmarks" --approve audio.playback
```

Seven fixed Persian phrases (greeting, confirmation, date and time, numbers, a
business sentence, Persian with an English term, a question) are synthesised
into WAV files in a folder **outside** the repository, with a `benchmark.json`
reporting synthesis time, audio duration, real-time factor, sample rate, file
size and peak memory where the standard library can measure it (it cannot on
Windows; the field is `null` with a note). Nothing is played automatically.

The report leaves `intelligible`, `numbers_pronounced_correctly`,
`mixed_language_behaviour`, `perceived_gender` and `classification` empty. The
person listens and decides: **Accept**, **Accept temporarily**, or **Reject**.
Primee does not decide. If rejected, the generic adapter and the text-only
fallback stay; Haaniye does not become a default.

## First listening result and tuning (2026-09-14)

The complete one-click flow ran on the target Windows 10 computer: 362 model
files verified, runtime created, sherpa-onnx 1.13.7 imported, the test
sentence spoken through `winsound`, temporary audio deleted. The person's
verdict, recorded in `profiles.py` as `listening_result`:

| Question | Answer |
| --- | --- |
| Intelligible | yes |
| Perceived gender | female (by listening; the documentation says nothing) |
| Fluency | choppy, broken delivery |
| Pronunciation | some words wrong |
| Classification | **accept temporarily** |

Two causes are under Primee's control and two are not:

- the worker synthesised sentence by sentence (`max_num_sentences=1`), which
  inserts pauses at every boundary: now `sentence_batch` (default 0 = whole
  text at once);
- the VITS sampling parameters were the engine defaults: now `speed`,
  `noise_scale`, `noise_scale_w` in `[voice]`, passed to the worker per request;
- the model is "low quality" by its own name: not fixable by settings;
- Persian writing omits short vowels, so the espeak-ng phonemiser guesses them
  for unknown words: mitigated, not solved, by an editable pronunciation
  lexicon (`config/voice-lexicon.example.toml`, `[voice] lexicon = ...`) that
  changes only what the engine hears, never what is shown or logged.

`primee voice tune --output <dir>` (or `TUNE_PRIMEE_VOICE_WINDOWS.cmd`) writes
the same sentence with five settings, opens the folder, and leaves the choice
to the listener; the chosen values go into `voice.local.toml`. Primee does not
choose. If the result stays unsatisfying, the next candidate is a Piper
`fa_IR` medium voice (22.05 kHz), which needs a separate licence decision: its
dataset licences are CC0 but the per-voice model licence is stated only at
repository level.

## Not yet done, and not claimed

- Speech-to-text, push-to-talk (F9 / Esc), the tkinter window, FFmpeg capture,
  half-duplex handling — none of it exists yet. `primee voice` prints a notice.
- No Schweizerdeutsch text-to-speech.
- The install and test flow has run once on the target Windows computer
  (2026-09-14). The tuning launcher and the lexicon have not run there yet;
  they are covered by tests with a stand-in worker only.
- Performance is unknown until the benchmark runs on the i5-5300U.
