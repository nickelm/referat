"""That `referat trim` cuts everything after a point and keeps none of it.

Build step 25, driven against a synthetic meeting in the scratch directory: a
transcript of eight entries across two speakers, two short WAVs, a pause, three
speaker snippets on either side of the cut, a `notes.md`, and a `synced`
status. Checks, in the base install and in about a second:

1. The wall-clock and audio-time conversions round-trip through the pause list,
   and an instant inside a pause maps to the second the pause began.
2. `plan` finds every line at or after the cut, the header, both WAVs, the
   snippets from after the cut, and the unnamed cluster whose only lines were
   after it - and never the named one.
3. `trim` writes `meta.json` first with a record that holds **no text**; the
   transcript keeps every line before the cut byte for byte, the header states
   the new length, both WAVs are truncated to the cut and still open, the
   snippets after the cut are gone and the ones before it stay, the cluster
   is dropped from the speakers block, the pause after the cut is gone, the
   duration and `ended_at` moved, and `synced` dropped to `transcribed`.
4. A second pass at the same point does nothing and writes nothing; an earlier
   cut appends a second record; a later one is refused as nothing to do.
5. A live meeting is refused, and so is a cut at zero.

Never reads or writes a real meeting.

    .venv\\Scripts\\python.exe scripts\\trim_fixture.py
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import wave
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from referat import trim  # noqa: E402
from referat.meeting import (  # noqa: E402
    ChannelAudio,
    Meeting,
    MeetingStatus,
    Pause,
    audio_to_wall,
    wall_to_audio,
)
from referat.transcribe import HEADER_RE, render_transcript  # noqa: E402

failures: list[str] = []


def check(condition: bool, what: str) -> None:
    print(("ok   " if condition else "MISS ") + what)
    if not condition:
        failures.append(what)


def write_wav(path: Path, seconds: float, rate: int) -> None:
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(rate)
        fh.writeframes(bytes(int(seconds * rate) * 2))


def frames_of(path: Path) -> int:
    with wave.open(str(path), "rb") as fh:
        return fh.getnframes()


ENTRIES = [
    (3.0, "Anna", "Let's start."),
    (40.0, "SPEAKER_02", "Sure."),
    (95.0, "Anna", "That is the decision, then."),
    (118.0, "SPEAKER_02", "Great, thanks everyone."),
    (130.0, "SPEAKER_03", "Did you see the email from HR?"),
    (150.0, "Anna", "Not yet. Is it bad?"),
    (170.0, "SPEAKER_03", "Between us, yes."),
    (185.0, "Anna", "Let's talk later."),
]
CUT = 130.0
"""Seconds of audio. The pause below sits at 60-70 wall, so this is 140 on the clock."""


def build(root: Path) -> tuple[Meeting, SimpleNamespace]:
    folder = root / "meetings" / "2026-09-10_1315"
    folder.mkdir(parents=True)
    (root / "staging").mkdir()
    started = dt.datetime(2026, 9, 10, 13, 15, 0)
    meeting = Meeting(
        dir=folder,
        id=folder.name,
        started_at=started,
        ended_at=started + dt.timedelta(seconds=210),
        duration_seconds=210.0,
        status=MeetingStatus.SYNCED,
        pauses=[Pause(60.0, 70.0), Pause(190.0, 195.0)],
        audio={
            "mic": ChannelAudio("mic.wav", 16000, frames=200 * 16000),
            "system": ChannelAudio("system.wav", 48000, frames=200 * 48000),
        },
        tags=["p"],
        speaker_names={"SPEAKER_01": "anna"},
        digest={"doc1": {"tab_id": "t", "notes_sha256": "x", "written_at": "y"}},
    )
    meeting.transcription = {
        "status": "transcribed",
        "channels": {
            "mic": {
                "speakers": {
                    "SPEAKER_01": {"snippets": [], "name": "anna"},
                    "SPEAKER_02": {
                        "snippets": [
                            {"file": "SPEAKER_02_1.wav", "start": 40.0, "end": 46.0},
                            {"file": "SPEAKER_02_2.wav", "start": 118.0, "end": 124.0},
                        ]
                    },
                    "SPEAKER_03": {
                        "snippets": [{"file": "SPEAKER_03_1.wav", "start": 130.0, "end": 136.0}]
                    },
                }
            }
        },
    }
    write_wav(folder / "mic.wav", 200.0, 16000)
    write_wav(folder / "system.wav", 200.0, 48000)
    meeting.speakers_dir.mkdir()
    for name in ("SPEAKER_02_1", "SPEAKER_02_2", "SPEAKER_03_1"):
        write_wav(meeting.speakers_dir / f"{name}.wav", 1.0, 16000)
    meeting.transcript_path.write_text(render_transcript(meeting, ENTRIES), encoding="utf-8")
    (folder / "notes.md").write_text("# Notes\n\nHR said something.\n", encoding="utf-8")
    meeting.save()
    config = SimpleNamespace(
        paths=SimpleNamespace(meetings_dir=root / "meetings", voices_dir=None),
        meeting_roots=lambda: [root / "meetings", root / "staging"],
        staging_dir=lambda: root / "staging",
        voices_dir=lambda: root / "voices",
    )
    return meeting, config


def main() -> int:
    # --- 1. The two clocks ---------------------------------------------------
    pauses = [Pause(60.0, 70.0), Pause(190.0, 195.0)]
    check(audio_to_wall(pauses, 30.0) == 30.0, "audio 30 is wall 30 before any pause")
    check(audio_to_wall(pauses, 60.0) == 60.0, "audio 60 maps to the instant the pause began")
    check(audio_to_wall(pauses, 61.0) == 71.0, "audio 61 is wall 71, across the pause")
    check(audio_to_wall(pauses, 130.0) == 140.0, "audio 130 is wall 140")
    check(wall_to_audio(pauses, 140.0) == 130.0, "wall 140 is audio 130")
    check(wall_to_audio(pauses, 65.0) == 60.0, "an instant inside a pause is the second it began")
    check(wall_to_audio(pauses, 200.0) == 185.0, "wall 200 is audio 185, across both pauses")
    check(
        all(wall_to_audio(pauses, audio_to_wall(pauses, a)) == a for a in (0.0, 59.0, 60.0, 61.0, 130.0, 189.0)),
        "wall_to_audio inverts audio_to_wall",
    )
    check(trim.parse_timestamp("00:02:10") == 130.0, "HH:MM:SS parses")
    check(trim.parse_timestamp("2:10") == 130.0, "MM:SS parses")
    check(trim.parse_timestamp("13:35") == 815.0, "a bare HH:MM is read as MM:SS - the clock is --clock")
    check(trim.parse_timestamp("2:70") is None, "seconds past 59 are refused")
    check(trim.parse_timestamp("13.35") is None, "a dotted time is refused")

    with tempfile.TemporaryDirectory(prefix="referat-trim-") as tmp:
        root = Path(tmp)
        meeting, config = build(root)
        original = meeting.transcript_path.read_text(encoding="utf-8")
        seconds, why = trim.clock_to_audio(meeting, "13:17:20")
        check(seconds == 130.0 and not why, "13:17:20 on the clock is audio 130, through the pause")
        seconds, why = trim.clock_to_audio(meeting, "13:00")
        check(seconds is None and "before the meeting started" in why, "a clock time before the start is refused")
        seconds, why = trim.clock_to_audio(meeting, "noon")
        check(seconds is None and "not a time of day" in why, "a non-time is refused")

        # --- 2. The plan -----------------------------------------------------
        decided, why = trim.plan(config, meeting, CUT)
        check(decided is not None and not why, f"plan at {CUT}: {why or 'ok'}")
        assert decided is not None
        check([e[1] for e in decided.entries] == ["00:02:10", "00:02:30", "00:02:50", "00:03:05"], "the four lines at or after the cut are found")
        check(decided.header is not None and "(2 min)" in decided.header[1], "the header is rewritten to the new length")
        check(decided.audio == {"mic": (200 * 16000, 130 * 16000), "system": (200 * 48000, 130 * 48000)}, "both WAVs are cut at the point")
        check(sorted(p.name for p in decided.snippets) == ["SPEAKER_03_1.wav"], "only the snippet from after the cut goes")
        check(decided.speakers_gone == ["SPEAKER_03"], "the unnamed cluster that spoke only after the cut is dropped")
        check("SPEAKER_01" not in decided.speakers_gone, "the named cluster is left alone")
        check(decided.pauses_dropped == 1, "the pause after the cut is dropped")
        check(decided.notes and decided.docs == 1, "the notes and the digest are noticed")
        check(decided.wall == 140.0, "the cut is 140 on the wall clock")
        text = trim.warning(meeting, decided)
        check("13:17:20" in text and "4 line(s)" in text and "SPEAKER_03" in text, "the warning says the clock time, the count and the cluster")
        check("HR" not in text, "the warning quotes no removed text")

        # --- 3. The cut ------------------------------------------------------
        done, message = trim.trim(config, meeting.id, CUT)
        check(done, f"trim: {message}")
        after = Meeting.load(meeting.dir)
        assert after is not None
        raw = json.loads(after.meta_path.read_text(encoding="utf-8"))
        record = raw["transcription"]["trimmed"]
        check(isinstance(record, list) and len(record) == 1, "one pass is recorded, as a list")
        check(record[0]["cut"] == "00:02:10" and record[0]["clock"] == "13:17:20", "the record names the cut both ways")
        check(record[0]["entries_removed"] == 4 and record[0]["speakers_removed"] == ["SPEAKER_03"], "the record counts")
        flat = json.dumps(raw)
        check("HR" not in flat and "Between us" not in flat, "meta.json holds none of the removed words")
        text = after.transcript_path.read_text(encoding="utf-8")
        check(HEADER_RE.match(text.split("\n")[0]) and "(2 min)" in text.split("\n")[0], "the header states 2 min")
        check(text.count("\n[") == 4 and "thanks everyone" in text and "HR" not in text, "four entries remain and none from after the cut")
        # Header, blank, then four entries each followed by a blank: lines 2-9.
        check(original.split("\n")[2:10] == text.split("\n")[2:10], "every kept line is byte-identical")
        check(frames_of(after.mic_path) == 130 * 16000 and frames_of(after.system_path) == 130 * 48000, "both WAVs are truncated and readable")
        check(after.audio["mic"].frames == 130 * 16000, "the audio block records the new length")
        check(not (after.dir / "mic.wav.tmp").exists(), "no temp file is left behind")
        check(sorted(p.name for p in after.speakers_dir.glob("*.wav")) == ["SPEAKER_02_1.wav", "SPEAKER_02_2.wav"], "the snippets before the cut stay")
        speakers = after.transcription["channels"]["mic"]["speakers"]
        check("SPEAKER_03" not in speakers and "SPEAKER_02" in speakers, "the cluster is dropped from the speakers block")
        check(after.duration_seconds == 140.0 and after.ended_at == dt.datetime(2026, 9, 10, 13, 17, 20), "duration and ended_at moved to the cut")
        check([p.start for p in after.pauses] == [60.0], "the pause after the cut is gone and the one before stays")
        check(after.status is MeetingStatus.TRANSCRIBED, "synced dropped back to transcribed")
        check((after.dir / "notes.md").exists(), "notes.md is left where it is")
        check(after.digest == meeting.digest, "the digest record is untouched")

        # --- 4. Again, earlier, later ----------------------------------------
        stamp = after.meta_path.stat().st_mtime_ns
        done, message = trim.trim(config, meeting.id, CUT)
        check(done and "nothing after" in message, f"a second pass at the same point: {message}")
        check(after.meta_path.stat().st_mtime_ns == stamp, "and it wrote nothing")
        done, message = trim.trim(config, meeting.id, 200.0)
        check(done and "nothing after" in message, "a cut past the new end is nothing to do")
        done, message = trim.trim(config, meeting.id, 100.0)
        check(done, f"an earlier cut: {message}")
        again = Meeting.load(meeting.dir)
        assert again is not None
        check(len(again.transcription["trimmed"]) == 2, "the second cut appends a second record")
        check(again.transcript_path.read_text(encoding="utf-8").count("\n[") == 3, "three entries remain")
        check(frames_of(again.mic_path) == 100 * 16000, "the WAV is cut again")
        check(again.speakers_dir.exists() is False or not list(again.speakers_dir.glob("SPEAKER_02_2*")), "the snippet at 118 goes with the earlier cut")

        # --- 5. Refusals -----------------------------------------------------
        decided, why = trim.plan(config, again, 0.0)
        check(decided is None and "after the start" in why, "a cut at zero is refused")
        again.status = MeetingStatus.TRANSCRIBING
        again.save()
        done, message = trim.trim(config, meeting.id, 50.0)
        check(not done and "transcribing" in message, "a live meeting is refused")

    print()
    if failures:
        print(f"{len(failures)} MISS")
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
