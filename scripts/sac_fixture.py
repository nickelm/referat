"""That a Smart App Control block costs speaker names *late* and never the audio.

On 2026-09-10 Smart App Control refused scipy's `_qhull` under pyannote,
diarization failed on both channels of a 47-minute Teams call, both transcripts
passed the quality gate, and the pipeline released 361 MB of audio thirteen
seconds later. The words survived; every remote speaker was one `REMOTE`, and
the only material that could ever have put names on them was gone. The block
lifted within the hour.

This checks the four answers to that, none of which needs torch:

1. :func:`referat.sac.is_block` and :func:`referat.sac.blocked_module` recognise
   a block through a cause chain and refuse everything else — a gated
   repository must not be retried.
2. :func:`referat.diarize.diarize` retries a block a bounded number of times
   and reports the file in `blocked`; a non-block fails once, with `blocked`
   empty.
3. :func:`referat.transcribe.audio_is_clean` keeps the audio when diarization
   *failed* on a channel, and still releases it when diarization was *skipped*
   — a channel with no voice in it is not a channel a rerun would improve.
4. :func:`referat.meeting.sac_block` and `undiarized_channels` read the record
   back the way the tray and `referat promote` do.

Everything runs against synthetic records in the scratch directory and never
touches a real meeting.

    .venv\\Scripts\\python.exe scripts\\sac_fixture.py
"""

from __future__ import annotations

import datetime as dt
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from referat import diarize, sac, transcribe  # noqa: E402
from referat.meeting import (  # noqa: E402
    ChannelAudio,
    Meeting,
    MeetingStatus,
    sac_block,
    undiarized_channels,
)

BLOCK = (
    "DLL load failed while importing _qhull: An Application Control policy has "
    "blocked this file."
)

failures: list[str] = []


def check(condition: bool, what: str) -> None:
    print(("ok   " if condition else "MISS ") + what)
    if not condition:
        failures.append(what)


# --- 1. Recognising a block ---------------------------------------------------

plain = ImportError(BLOCK)
check(sac.is_block(plain), "a bare block is a block")
check(sac.blocked_module(plain) == "_qhull", "the module is read out of the message")

try:
    try:
        raise ImportError(BLOCK)
    except ImportError as inner:
        raise RuntimeError("pyannote could not import its clustering") from inner
except RuntimeError as wrapped:
    check(sac.is_block(wrapped), "a block wrapped in a library's RuntimeError is still a block")
    check(sac.blocked_module(wrapped) == "_qhull", "and still names its file")

gated = RuntimeError("could not load pyannote/speaker-diarization-community-1; check the token")
check(not sac.is_block(gated), "a gated repository is not a block")
check(sac.blocked_module(gated) == "", "and names no file")

# --- 2. The bounded retry -----------------------------------------------------


class _Config(SimpleNamespace):
    def hf_token(self) -> str:
        return "hf_synthetic"


config = _Config(
    transcription=SimpleNamespace(diarization=True, diarization_model="synthetic/model"),
    paths=SimpleNamespace(hf_token_file=Path("nowhere")),
)
audio = np.zeros(16000, dtype=np.float32)

calls: list[int] = []
waits: list[str] = []
diarize.BLOCK_RETRY_SECONDS = 0.0


def always_blocked(*_args: object) -> tuple[list[diarize.Turn], dict[str, np.ndarray]]:
    calls.append(1)
    raise ImportError(BLOCK)


diarize._run = always_blocked  # type: ignore[assignment]
result = diarize.diarize(audio, 16000, config, "cuda", on_wait=waits.append)
check(result.status == diarize.FAILED, "a block that never lifts fails")
check(result.blocked == "_qhull", "and records the file it failed on")
check(len(calls) == 1 + diarize.BLOCK_RETRIES, f"after {1 + diarize.BLOCK_RETRIES} attempts, not more")
check(len(waits) == diarize.BLOCK_RETRIES, "each wait was reported to the caller")
check(
    "blocked" in result.to_json() and result.to_json()["blocked"] == "_qhull",
    "the block reaches meta.json",
)

calls.clear()


def gated_forever(*_args: object) -> tuple[list[diarize.Turn], dict[str, np.ndarray]]:
    calls.append(1)
    raise RuntimeError("could not load synthetic/model; check the token and the repo conditions")


diarize._run = gated_forever  # type: ignore[assignment]
result = diarize.diarize(audio, 16000, config, "cuda")
check(result.status == diarize.FAILED and result.blocked == "", "a non-block fails with no file named")
check(len(calls) == 1, "and is not retried")

calls.clear()


def lifts_on_second(*_args: object) -> tuple[list[diarize.Turn], dict[str, np.ndarray]]:
    calls.append(1)
    if len(calls) == 1:
        raise ImportError(BLOCK)
    return [diarize.Turn(0.0, 1.0, "SPEAKER_00")], {"SPEAKER_00": np.ones(4, dtype=np.float32)}


diarize._run = lifts_on_second  # type: ignore[assignment]
result = diarize.diarize(audio, 16000, config, "cuda")
check(result.ok and len(result.turns) == 1, "a block that lifts on the retry succeeds")
check(result.blocked == "", "with nothing recorded as blocked")

# --- 3 and 4. The gate, and reading the record back ---------------------------


def synthetic(folder: Path, mic: dict, system: dict) -> Meeting:
    meeting = Meeting(
        dir=folder,
        id=folder.name,
        started_at=dt.datetime(2026, 9, 10, 9, 3),
        ended_at=dt.datetime(2026, 9, 10, 9, 50),
        duration_seconds=2822.0,
        status=MeetingStatus.TRANSCRIBED,
        audio={
            "mic": ChannelAudio(file="mic.wav", samplerate=16000),
            "system": ChannelAudio(file="system.wav", samplerate=48000),
        },
        transcription={"status": "transcribed", "channels": {"mic": mic, "system": system}},
    )
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "mic.wav").write_bytes(b"")
    (folder / "system.wav").write_bytes(b"")
    return meeting


with tempfile.TemporaryDirectory(prefix="referat-sac-") as scratch:
    root = Path(scratch)

    blocked_run = synthetic(
        root / "2026-09-10_0903",
        {"clean": True, "silent": False, "diarization": {"status": "failed", "reason": BLOCK, "blocked": "_qhull"}},
        {"clean": True, "silent": False, "diarization": {"status": "failed", "reason": BLOCK, "blocked": "_qhull"}},
    )
    check(not transcribe.audio_is_clean(blocked_run), "the 2026-09-10 meeting keeps its audio")
    check(undiarized_channels(blocked_run) == ["mic", "system"], "both channels are named as undiarized")
    check(sac_block(blocked_run) == "_qhull", "and the block is read back for the tray")

    in_person = synthetic(
        root / "2026-09-08_0902",
        {"clean": True, "silent": False, "diarization": {"status": "done", "model": "m", "device": "cuda", "seconds": 1.0, "turns": 40}},
        {"clean": True, "silent": True, "diarization": {"status": "skipped", "reason": "no voice in the channel"}},
    )
    check(transcribe.audio_is_clean(in_person), "a meeting held in person, loopback skipped, still releases")
    check(undiarized_channels(in_person) == [], "a skipped channel is not undiarized")
    check(sac_block(in_person) == "", "and nothing is blocked")

    no_token = synthetic(
        root / "2026-09-08_0959",
        {"clean": True, "silent": False, "diarization": {"status": "skipped", "reason": "no Hugging Face token"}},
        {"clean": True, "silent": False, "diarization": {"status": "skipped", "reason": "no Hugging Face token"}},
    )
    check(transcribe.audio_is_clean(no_token), "diarization deliberately off still releases")

    other_failure = synthetic(
        root / "2026-09-08_1102",
        {"clean": True, "silent": False, "diarization": {"status": "failed", "reason": "RuntimeError: could not load the model"}},
        {"clean": True, "silent": False, "diarization": {"status": "done", "model": "m", "device": "cuda", "seconds": 1.0, "turns": 4}},
    )
    check(not transcribe.audio_is_clean(other_failure), "any diarization failure keeps the audio, block or not")
    check(undiarized_channels(other_failure) == ["mic"], "and names only the channel that failed")
    check(sac_block(other_failure) == "", "but the tray is not told to wait for a block that was not one")

print()
if failures:
    print(f"{len(failures)} miss(es)")
    sys.exit(1)
print("all checks passed")
