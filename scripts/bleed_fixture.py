r"""A synthetic hybrid meeting, to check that :mod:`referat.bleed` drops the right lines.

    .venv\Scripts\python.exe scripts\bleed_fixture.py

Exits non-zero on any miss, so it reads as a measurement and runs as a check.

**Two lists of segments, deliberately not two WAVs.** The unit under test takes
`ChannelTranscript` objects and never touches audio, so this whole file runs in
the base install in about a second instead of behind three gigabytes of wheels.
That is the payoff for `bleed.py` importing nothing but the standard library, and
it is the same trade :mod:`referat.hotwords` makes by estimating token counts
rather than loading a tokenizer.

What is being checked is not that the numbers are *right* — nothing here can
establish that, because the only real hybrid meeting on this machine had its
audio released and cannot be re-run. It is that each rule fires on the case it
was written for and, more to the point, **stays off the cases it must never
touch**: a room speaker who talks over the far end continuously, the owner's own
cluster, backchannels short enough that nothing distinguishes a genuine second
"Yeah" from an echo of the first, and the whole-channel case where the rule
convicting everybody is likelier to be a bug than a meeting.

The populations below are built to look like `2026-09-02_1059`: the echo copies
are lowercased, stripped of punctuation, given the stutters that the microphone
copy kept and the loopback copy collapsed, shifted by up to two seconds, and
sometimes split in two, because that is what the two decodes of one utterance
actually differed by.
"""

from __future__ import annotations

import sys

from referat.bleed import apply, suppress
from referat.config import BleedConfig
from referat.transcribe import ChannelTranscript, Segment
from referat.voices import Cluster

SETTINGS = BleedConfig()
OWNER = "Niklas"

LINES = [
    "I understand that this is just a placeholder for the real section",
    "This is no longer reflected in the current set of puzzles we built",
    "And then in the methods we go to explore this design space properly",
    "Or is this something that goes into maybe section three of the paper",
    "So what they don't want is that you have a long text to read first",
    "I think I would agree with this probably for most of the experiments",
    "We said we need puzzles that are independent of the literacy question",
    "Just to know what was based on our discussion and what was edited later",
]

ROOM_LINES = [
    "Let me pull up the other document and see what it says about that",
    "We could restructure the whole second half if that would help you",
    "My feeling is that the deadline is the thing forcing this decision",
    "That would mean rewriting the evaluation section from scratch again",
]


def seg(start: float, end: float, text: str, speaker: str) -> Segment:
    """One segment, with the quality numbers a clean transcription would produce."""
    return Segment(
        start=start,
        end=end,
        text=text,
        avg_logprob=-0.21,
        compression_ratio=1.8,
        no_speech_prob=0.05,
        speaker=speaker,
    )


def echoed(text: str) -> str:
    """`text` as the microphone heard it back off a loudspeaker.

    Lowercased, unpunctuated and stuttered — the three ways the two decodes of one
    utterance actually differed. The stutter is the important one: it is why
    :func:`referat.bleed.containment` counts repeats rather than comparing sets,
    since `"that, that, that"` against `"that"` must not score 1.0.
    """
    words = text.lower().replace(",", "").split()
    if len(words) > 4:
        words = words[:2] + [words[2], words[2]] + words[2:]
    return " ".join(words)


def build() -> tuple[ChannelTranscript, ChannelTranscript]:
    """The two channels of a meeting with two people in the room and two on a call."""
    mic: list[Segment] = []
    system: list[Segment] = []
    at = 10.0

    # The far end, on the loopback, and its echo on the microphone. The echo is
    # clustered on its own, which is what the cluster rule is for.
    for i, line in enumerate(LINES):
        system.append(seg(at, at + 6.0, line, "SPEAKER_05"))
        if i % 3 == 2:
            # One channel splits what the other emitted whole, which is where the
            # long gaps between two copies of one utterance come from.
            half = echoed(line).split()
            mic.append(seg(at - 1.4, at + 3.0, " ".join(half[: len(half) // 2]), "SPEAKER_03"))
            mic.append(seg(at + 3.0, at + 7.2, " ".join(half[len(half) // 2 :]), "SPEAKER_03"))
        else:
            mic.append(seg(at - 1.1, at + 5.4, echoed(line), "SPEAKER_03"))
        at += 12.0

    # Room speaker A, who never overlaps the call at all.
    for i, line in enumerate(ROOM_LINES * 10):
        mic.append(seg(at, at + 5.0, f"{line} number {i}", "SPEAKER_01"))
        at += 6.0

    # Room speaker B: the risk case. Talks *through* the far end, so almost all of
    # their speech time sits under loopback speech — and says nothing the far end
    # says, which is the only thing separating them from an echo.
    covered = 10.0
    for i in range(30):
        mic.append(
            seg(covered, covered + 4.0, f"{ROOM_LINES[i % 4]} as I was saying {i}", "SPEAKER_02")
        )
        covered += 3.2

    # The owner, deliberately built to look exactly like echo: their lines *are*
    # the far end's lines, over the far end's time. Only the refusal saves them.
    for i, line in enumerate(LINES):
        mic.append(seg(10.0 + i * 12.0, 15.0 + i * 12.0, echoed(line), "SPEAKER_04"))

    # Backchannels on both channels, short enough that no text rule may touch them.
    back = 500.0
    for i in range(100):
        mic.append(seg(back, back + 0.8, "Yeah.", "SPEAKER_01"))
        system.append(seg(back + 0.2, back + 1.0, "Yeah.", "SPEAKER_05"))
        back += 1.5

    # Loose echo: the far end's words, filed under room speaker A because
    # diarization put the microphone's copy in with the person sitting next to the
    # loudspeaker. The cluster rule cannot reach these; the per-segment rule must.
    loose = 900.0
    for line in LINES[:5]:
        system.append(seg(loose, loose + 6.0, line, "SPEAKER_05"))
        mic.append(seg(loose - 0.6, loose + 5.2, echoed(line), "SPEAKER_01"))
        loose += 12.0

    # The names identification resolved, which is how the owner refusal finds the
    # cluster it must spare. SPEAKER_03 carries a name too, and a real one: the
    # echo of a remote person is what somebody labeling this meeting would have
    # been offered, and would correctly have called Benjamin. That is exactly how
    # the two poisoned voiceprints got into the database.
    return (
        ChannelTranscript(
            channel="mic",
            label="ME",
            voiced_seconds=900.0,
            peak=0.7,
            segments=sorted_by_start(mic),
            speakers={
                "SPEAKER_01": Cluster(label="SPEAKER_01", name="Gaby"),
                "SPEAKER_02": Cluster(label="SPEAKER_02", name=""),
                "SPEAKER_03": Cluster(label="SPEAKER_03", name="Benjamin"),
                "SPEAKER_04": Cluster(label="SPEAKER_04", name=OWNER),
            },
        ),
        ChannelTranscript(
            channel="system",
            label="REMOTE",
            voiced_seconds=400.0,
            peak=0.6,
            segments=sorted_by_start(system),
            speakers={"SPEAKER_05": Cluster(label="SPEAKER_05", name="Benjamin")},
        ),
    )


def sorted_by_start(segments: list[Segment]) -> list[Segment]:
    return sorted(segments, key=lambda s: (s.start, s.end))


def counts(transcript: ChannelTranscript) -> dict[str, int]:
    out: dict[str, int] = {}
    for segment in transcript.segments:
        out[segment.speaker] = out.get(segment.speaker, 0) + 1
    return out


def check(name: str, got: object, want: object) -> bool:
    ok = got == want
    print(f"  {'ok  ' if ok else 'MISS'}  {name}: {got!r}" + ("" if ok else f" (want {want!r})"))
    return ok


def main() -> int:
    mic, system = build()
    before = counts(mic)
    verdict = suppress([mic, system], SETTINGS, OWNER)
    kept_mic, _ = apply([mic, system], verdict)
    after = counts(kept_mic)

    print(f"\nmic {len(mic.segments)} segment(s), loopback {len(system.segments)}")
    print(f"verdict: {verdict.status}, dropped {verdict.dropped}, kept {verdict.kept}\n")
    for label in sorted(verdict.clusters):
        v = verdict.clusters[label]
        print(
            f"  {label}  {'ECHO' if v.echo else 'kept'}  n={v.segments:3d}  "
            f"time={v.time_covered:.2f}  text={v.text_matched:.2f}  {v.name or '-'}  {v.why}"
        )
    print()

    ok = [
        check("the echo cluster SPEAKER_03 is convicted", verdict.clusters["SPEAKER_03"].echo, True),
        check("all of SPEAKER_03's segments go", after.get("SPEAKER_03", 0), 0),
        check(
            "room speaker A survives except its loose echo",
            after.get("SPEAKER_01", 0),
            before["SPEAKER_01"] - 5,
        ),
        check(
            "room speaker B survives whole, talking over the call",
            after.get("SPEAKER_02", 0),
            before["SPEAKER_02"],
        ),
        check(
            "the owner's cluster survives whole despite looking like echo",
            after.get("SPEAKER_04", 0),
            before["SPEAKER_04"],
        ),
        check("the owner's cluster was refused by name", verdict.clusters["SPEAKER_04"].echo, False),
        check("exactly the 5 loose echo segments were found", len(verdict.segments), 5),
        check(
            "no loopback segment was dropped",
            len(apply([mic, system], verdict)[1].segments),
            len(system.segments),
        ),
    ]

    # The backchannels are the one population with a number rather than a
    # boolean behind it: 100 "Yeah." on each channel, overlapping in time and
    # byte-identical, and not one may be dropped by the per-segment rule.
    dropped_backchannels = sum(
        1 for i in verdict.segments if len(mic.segments[i].text.split()) < SETTINGS.min_tokens
    )
    ok.append(check("no backchannel was dropped by text", dropped_backchannels, 0))

    # The whole-channel refusal, checked on its own meeting: a microphone holding
    # nothing but echo. Convicting every cluster is likelier to be a bug in the two
    # rules than a real meeting, and costs the entire channel when it is.
    all_echo = ChannelTranscript(
        channel="mic",
        label="ME",
        voiced_seconds=900.0,
        peak=0.7,
        segments=[s for s in mic.segments if s.speaker == "SPEAKER_03"],
    )
    refused = suppress([all_echo, system], SETTINGS, OWNER)
    ok.append(check("a wholly-echo microphone is refused", refused.dropped, 0))
    ok.append(
        check(
            "...and says why",
            refused.clusters["SPEAKER_03"].why,
            "every microphone cluster looked like echo",
        )
    )

    # And the ordinary case, which is every meeting held in person: a loopback
    # that recorded silence. The rule must never fire, and must say it skipped
    # rather than that it found nothing.
    silent = ChannelTranscript(
        channel="system", label="REMOTE", voiced_seconds=0.2, peak=0.01, segments=[]
    )
    skipped = suppress([mic, silent], SETTINGS, OWNER)
    ok.append(check("an in-person meeting is skipped", skipped.status, "skipped"))

    print(f"\n{sum(ok)}/{len(ok)} checks passed")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
