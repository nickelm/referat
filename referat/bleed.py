"""Suppressing the microphone's copy of speech that arrived through the loopback.

A meeting whose remote audio comes out of a speaker in the same room as the
microphone is recorded twice: once digitally, on the WASAPI loopback, and once
acoustically, when the room microphone hears the speaker. Both channels are
transcribed and both are diarized, so the far end ends up in `transcript.md`
twice — and because both channels are labeled by hand, twice under *one name*,
which is what makes the duplication invisible rather than obvious.

Measured on `2026-09-02_1059`, a hybrid meeting whose loopback followed the
default output to a television standing in the room: 2200 entries, 1722 of them
from the microphone and 478 from the loopback, with 1239 lines under a remote
speaker's name. Roughly **761 of those lines were the microphone's second copy**.

**Suppression is one-directional and that is the whole safety argument.**
Microphone segments may be dropped; loopback segments are never dropped, on any
path, by any rule in this module. The reason is not that the loopback transcribes
better — it does, being a tap on the render mix before the DAC rather than the
same signal after a loudspeaker, three metres of room and an AGC, but that is a
convenience. The reason is asymmetric cost: a conferencing application does not
loop your own capture back into the render mix, so **speech from the room can
only ever exist on the microphone**. Dropping a microphone segment risks losing a
second copy of something. Dropping a loopback segment risks losing the only copy
of the far end.

Two mechanisms, in this order, because they answer different questions.

The **cluster rule** is primary. A microphone cluster whose speech time sits
under loopback speech *and* whose text is matched by loopback text is an echo
cluster, and every one of its segments goes. Both conditions, and the "and" is
the design: the microphone in a room is voiced almost continuously — 97% of that
meeting — so time alone convicts anybody who talks while the far end talks, while
text alone misses a cluster that is mostly "Yeah." and "Okay.". Together they say
something much more specific: *this cluster is saying what the loopback is
saying, when the loopback is saying it*, which is what an echo is. Somebody
talking **over** the far end scores high on time and near zero on text, and that
is the case this exists to spare.

The **per-segment rule** is the complement, for echo that diarization filed
under a room person's cluster rather than into one of its own. That really
happens: `[00:19:33] Benjamin:` and `[00:19:33] Niklas:` in that meeting are the
same sentence, verbatim, because the microphone's copy was clustered with the
person sitting next to the television.

**Nothing shorter than `min_tokens` is ever dropped by text.** More than half of
a hybrid meeting is one- and two-token backchannels, and nothing distinguishes a
remote person's second "Yeah" from the microphone's copy of their first. Those go
only when the cluster rule takes them, which drops them for *whose voice they
are* rather than for what they say. That division of labour is why there are two
mechanisms rather than one tuned somewhere in between.

**No runtime import of :mod:`referat.transcribe`,** exactly as in
:mod:`referat.merge`: the types come in under `TYPE_CHECKING` and this module is
pure standard library. That is deliberate rather than tidy — it is what lets
`scripts/bleed_fixture.py` exercise every rule here in a second, in the base
install, instead of behind three gigabytes of wheels. It is the same trade
:mod:`referat.hotwords` makes when it estimates token counts rather than loading
a tokenizer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from referat.config import BleedConfig
    from referat.transcribe import ChannelTranscript, Segment

log = logging.getLogger(__name__)

MIC_CHANNEL = "mic"
SYSTEM_CHANNEL = "system"
"""The two `path.stem` values :func:`referat.transcribe.transcribe_channels` produces.

Named here rather than matched on `label`, because `ME` and `REMOTE` are the
*fallback* labels for a segment nothing attributed and are absent from any
segment diarization did attribute. The channel is the durable fact.
"""

_PUNCTUATION = "\"'`.,!?;:()[]{}<>-–—…/\\*_"
"""Stripped from the ends of a token before comparison.

Not from the middle: `don't` and `dont` should compare equal, but so should
`e.g.` with itself, and a rule that removed interior punctuation would also merge
`re-run` into `rerun` and lose the distinction between `A.M.` and `AM`. The ends
are where Whisper's two decodes of one utterance actually differ — one channel
gets a comma the other does not — and that is the whole problem being solved.
"""


# --- Comparing two utterances -----------------------------------------------


def tokens(text: str) -> list[str]:
    """`text` as lowercased, end-stripped words, for containment comparison.

    Empty tokens are dropped, so a stray `--` between two words does not become a
    token that neither copy of the utterance can match.
    """
    return [stripped for word in text.lower().split() if (stripped := word.strip(_PUNCTUATION))]


def containment(a: list[str], b: list[str]) -> float:
    """What fraction of `a`'s tokens appear in `b`, counting repeats.

    A multiset containment rather than a set one, so `"that, that, that"` against
    `"that"` scores 1/3 and not 1.0. Whisper's two decodes of one utterance
    disagree about stutters more than about anything else — the microphone copy
    keeps them and the loopback copy tends to collapse them — and a set measure
    would call a three-fold repetition a perfect match with its own single
    occurrence.

    Zero for an empty `a`, which keeps the caller from having to special-case it.
    """
    if not a:
        return 0.0
    remaining: dict[str, int] = {}
    for token in b:
        remaining[token] = remaining.get(token, 0) + 1
    matched = 0
    for token in a:
        if remaining.get(token, 0) > 0:
            remaining[token] -= 1
            matched += 1
    return matched / len(a)


def duplicates(a: str, b: str, settings: BleedConfig) -> bool:
    """Whether `a` is the same utterance as `b`, heard on the other channel.

    Containment in **both** directions, and the reverse direction is not symmetry
    for its own sake. Forward alone lets a short line be swallowed by any longer
    line nearby that happens to contain its words: measured on a real transcript,
    forward-only produced 21 pairings of one room speaker against another, which
    the reverse requirement cut to 8.

    Both texts must reach `min_tokens`. Below it there is nothing to be confident
    about — see the module docstring on backchannels.
    """
    left, right = tokens(a), tokens(b)
    if len(left) < settings.min_tokens or len(right) < settings.min_tokens:
        return False
    return (
        containment(left, right) >= settings.contain
        and containment(right, left) >= settings.back_contain
    )


# --- Where the loopback was speaking ----------------------------------------


def _spans(segments: list[Segment], pad: float) -> list[tuple[float, float]]:
    """The segments' intervals, padded on both sides and merged where they touch.

    `pad` absorbs the offset between the two recordings of one moment. The
    loopback tap is before the DAC, so its copy usually starts a little first, and
    Whisper cuts the two channels independently — on the meeting this was built
    for, the loopback's 478 segments spanned 3151 seconds against 1327 seconds of
    voiced audio, roughly 2.4x, because its segmentation ran long.

    That inflation is why :func:`cluster_verdicts` cannot lean on time alone: it
    makes the loopback look like it was speaking almost continuously, which
    flatters every microphone cluster equally.
    """
    if not segments:
        return []
    intervals = sorted((s.start - pad, s.end + pad) for s in segments)
    merged = [intervals[0]]
    for start, end in intervals[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _covered(start: float, end: float, spans: list[tuple[float, float]]) -> float:
    """How many seconds of `start`..`end` fall inside `spans`.

    Linear over the spans, which are merged and sorted. A meeting is a few
    thousand of each, so the quadratic pass costs milliseconds and buys code you
    can read — the same trade :func:`referat.diarize._dominant_speaker` makes.
    """
    total = 0.0
    for span_start, span_end in spans:
        if span_start >= end:
            break
        overlap = min(end, span_end) - max(start, span_start)
        if overlap > 0:
            total += overlap
    return total


def _overlaps(a: Segment, b: Segment, pad: float) -> bool:
    """Whether two segments describe the same moment, within `pad` on each side.

    An interval test rather than a comparison of start times. The gap between two
    copies of one utterance was measured at a median of 2 seconds but a maximum of
    34, and the long ones are precisely where one channel emitted a single run-on
    segment covering what the other split into several. A start-gap threshold wide
    enough for those would be wide enough to catch unrelated speech; an overlap
    test handles both, because a long segment only overlaps a distant one if it
    genuinely spans the distance.
    """
    return a.start - pad < b.end and b.start - pad < a.end


# --- The cluster rule -------------------------------------------------------


@dataclass(frozen=True)
class ClusterVerdict:
    """What was measured about one microphone cluster, and what was decided."""

    label: str
    """The `SPEAKER_NN` this cluster carries on the microphone channel."""
    name: str = ""
    """What identification resolved it to, or empty while it is a number."""
    segments: int = 0
    speech_seconds: float = 0.0
    time_covered: float = 0.0
    """Of this cluster's speech time, the fraction sitting under loopback speech."""
    text_matched: float = 0.0
    """Of its long-enough lines, the fraction a loopback line duplicates."""
    echo: bool = False
    why: str = ""
    """Empty when convicted; the reason otherwise, so a refusal is legible."""
    spared: bool = False
    """Whether this cluster is off-limits to the per-segment rule as well.

    **Two kinds of refusal, and the difference is load-bearing.** A cluster that
    simply did not match enough of the loopback is *cleared*, and its segments stay
    eligible for :func:`echo_segments` — that is the whole point of the second
    mechanism, since a room speaker's cluster is exactly where misfiled echo hides.

    A cluster refused *protectively* — the owner's, or every cluster when the rule
    convicted the entire channel — is `spared`, and nothing may touch its segments.
    Without this distinction the per-segment rule undoes both refusals one line at
    a time, which is not a hypothetical: the fixture caught it doing precisely that,
    deleting the owner's cluster after the cluster rule had explicitly saved it.
    A refusal that only holds at one level is not a refusal.
    """

    def to_json(self) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "verdict": "echo" if self.echo else "spared" if self.spared else "kept",
            "segments": self.segments,
            "speech_seconds": round(self.speech_seconds, 2),
            "time_covered": round(self.time_covered, 3),
            "text_matched": round(self.text_matched, 3),
        }
        if self.name:
            meta["name"] = self.name
        if self.why:
            meta["why"] = self.why
        return meta


def cluster_verdicts(
    mic: ChannelTranscript,
    system: ChannelTranscript,
    settings: BleedConfig,
    owner: str = "",
) -> dict[str, ClusterVerdict]:
    """Measure every microphone cluster against the loopback, and judge it.

    A cluster is echo when `time_covered` reaches `cluster_time` **and**
    `text_matched` reaches `cluster_text`. See the module docstring for why both.

    Clusters are read off `Segment.speaker` rather than off
    :attr:`referat.diarize.Diarization.turns`, for two reasons. The turns are
    keyed by pyannote's own labels and the map that renames them to `SPEAKER_NN`
    is local to :func:`referat.transcribe._diarize_into` and thrown away; and it
    is the *segments* that get dropped, so segment coverage is the coverage that
    decides. It also means this function needs nothing but two lists of segments,
    which is what makes it testable without audio.

    Every cluster gets a verdict, convicted or not. The ones that were kept are
    the only material that will ever calibrate these two thresholds, which is why
    they are recorded rather than discarded — the same reason
    :class:`referat.voices.Match` is written to `meta.json` even when it was
    refused.
    """
    spans = _spans(system.segments, settings.window)
    grouped: dict[str, list[Segment]] = {}
    for segment in mic.segments:
        if segment.speaker:
            grouped.setdefault(segment.speaker, []).append(segment)

    verdicts: dict[str, ClusterVerdict] = {}
    for label, segments in grouped.items():
        speech = sum(max(0.0, s.end - s.start) for s in segments)
        covered = sum(_covered(s.start, s.end, spans) for s in segments)
        long_enough = [s for s in segments if len(tokens(s.text)) >= settings.min_tokens]
        matched = sum(
            1
            for s in long_enough
            if any(
                _overlaps(s, other, settings.window) and duplicates(s.text, other.text, settings)
                for other in system.segments
            )
        )
        name = _resolved_name(mic, label)
        verdict = ClusterVerdict(
            label=label,
            name=name,
            segments=len(segments),
            speech_seconds=speech,
            time_covered=covered / speech if speech else 0.0,
            text_matched=matched / len(long_enough) if long_enough else 0.0,
        )
        # The owner is holding the microphone. A cluster matched to them is room
        # speech by definition, and a false positive here would delete the user's
        # own words out of their own meeting -- the one refusal stated as a person
        # rather than as a number.
        if owner and name and name.casefold() == owner.strip().casefold():
            verdict = replace(
                verdict, spared=True, why="resolved to the owner, who is in the room"
            )
        elif not long_enough:
            # Nothing long enough to compare, so `text_matched` is 0.0 by
            # construction rather than by measurement. Saying so is the difference
            # between a cluster that was cleared and one that was never examined.
            verdict = replace(verdict, why="no line long enough to compare")
        elif (
            verdict.time_covered >= settings.cluster_time
            and verdict.text_matched >= settings.cluster_text
        ):
            verdict = replace(verdict, echo=True)
        else:
            verdict = replace(verdict, why="not enough of it matches the loopback")
        verdicts[label] = verdict

    # A microphone that is entirely echo is a real thing -- a call played into an
    # empty room -- but it is also exactly what a bug in the two rules above looks
    # like, and being wrong costs the whole channel rather than part of it. Refuse
    # and say so, the way `voices.bootstrap_owner` refuses rather than guessing.
    #
    # Spared rather than merely cleared, or the per-segment rule picks the channel
    # apart line by line and the refusal buys nothing at all.
    if verdicts and all(v.echo for v in verdicts.values()):
        return {
            label: replace(
                v, echo=False, spared=True, why="every microphone cluster looked like echo"
            )
            for label, v in verdicts.items()
        }
    return verdicts


def _resolved_name(transcript: ChannelTranscript, label: str) -> str:
    """The name identification put on one cluster, or empty when it is still a number."""
    cluster = (transcript.speakers or {}).get(label)
    return getattr(cluster, "name", "") or ""


# --- The per-segment rule ---------------------------------------------------


def echo_segments(
    mic: list[Segment], system: list[Segment], settings: BleedConfig
) -> dict[int, int]:
    """Microphone segments that duplicate a loopback segment, by index.

    The complement to the cluster rule, for echo that diarization filed under a
    room person rather than into a cluster of its own. Returns a map from the
    index in `mic` to the index in `system` it duplicates, so the caller can say
    which line justified each removal rather than only how many there were.

    Each loopback segment may justify several microphone segments — one channel
    routinely splits what the other emitted whole — but each microphone segment is
    matched at most once, to the first loopback segment that satisfies both
    :func:`_overlaps` and :func:`duplicates`.
    """
    found: dict[int, int] = {}
    for index, segment in enumerate(mic):
        for other, candidate in enumerate(system):
            if _overlaps(segment, candidate, settings.window) and duplicates(
                segment.text, candidate.text, settings
            ):
                found[index] = other
                break
    return found


# --- The verdict for the whole meeting --------------------------------------


@dataclass(frozen=True)
class Suppression:
    """What suppression decided about one meeting, and what it would remove.

    Returned by :func:`suppress`, which changes nothing; :func:`apply` is what
    acts on it. Splitting the verdict from the act is the move
    :func:`referat.transcribe.audio_is_clean` made out of `release_audio_if_clean`
    and for the same reason: this object is what goes into `meta.json` and what a
    script can measure, whether or not anything acted on it.
    """

    status: str = "skipped"
    """`suppressed`, `clean` when it ran and found nothing, or `skipped`."""
    reason: str = ""
    """Why it did not run, when it did not."""
    clusters: dict[str, ClusterVerdict] = field(default_factory=dict)
    segments: dict[int, int] = field(default_factory=dict)
    """Loose microphone segments, by index, mapped to the loopback segment they duplicate."""
    dropped: int = 0
    dropped_seconds: float = 0.0
    kept: int = 0

    @property
    def echo_clusters(self) -> list[str]:
        return sorted(label for label, v in self.clusters.items() if v.echo)

    def to_json(self) -> dict[str, Any]:
        meta: dict[str, Any] = {"status": self.status}
        if self.reason:
            meta["reason"] = self.reason
        if self.clusters:
            meta["clusters"] = {
                label: v.to_json() for label, v in sorted(self.clusters.items())
            }
        meta.update(
            {
                "dropped_segments": self.dropped,
                "dropped_seconds": round(self.dropped_seconds, 2),
                "kept_segments": self.kept,
                "loose_segments": len(self.segments),
            }
        )
        return meta


def suppress(
    transcripts: list[ChannelTranscript], settings: BleedConfig, owner: str = ""
) -> Suppression:
    """Decide what of the microphone channel is the loopback coming back.

    Changes nothing. The three refusals below cost nothing but a `skipped` status
    and a reason, which is the ordinary outcome for every meeting held in person:
    a silent loopback has nothing to compare against, and the rule can never fire.
    """
    if not settings.suppress:
        return Suppression(reason="[bleed].suppress is off in the config")
    mic = _channel(transcripts, MIC_CHANNEL)
    system = _channel(transcripts, SYSTEM_CHANNEL)
    if mic is None or system is None:
        return Suppression(reason="the meeting does not have both channels")
    if not mic.segments or not system.segments:
        return Suppression(reason="one of the channels transcribed to nothing")
    if system.quality.silent:
        return Suppression(reason="the loopback channel held no voice")

    verdicts = cluster_verdicts(mic, system, settings, owner)
    echo = {label for label, v in verdicts.items() if v.echo}
    # Convicted clusters are already gone, and *spared* ones are off-limits -- see
    # `ClusterVerdict.spared`. What is left is the clusters that were merely
    # cleared, which is where misfiled echo actually hides.
    spared = {label for label, v in verdicts.items() if v.spared}
    eligible = [
        i for i, s in enumerate(mic.segments) if s.speaker not in echo and s.speaker not in spared
    ]
    loose_by_index = echo_segments([mic.segments[i] for i in eligible], system.segments, settings)
    # Re-keyed onto the full microphone list, so an index in `segments` means the
    # same thing as an index in `mic.segments` and `apply` needs no second pass to
    # work out which is which.
    loose = {eligible[i]: j for i, j in loose_by_index.items()}

    removed = [s for i, s in enumerate(mic.segments) if s.speaker in echo or i in loose]
    seconds = sum(max(0.0, s.end - s.start) for s in removed)
    return Suppression(
        status="suppressed" if removed else "clean",
        clusters=verdicts,
        segments=loose,
        dropped=len(removed),
        dropped_seconds=seconds,
        kept=len(mic.segments) - len(removed),
    )


def apply(
    transcripts: list[ChannelTranscript], verdict: Suppression
) -> list[ChannelTranscript]:
    """The channels with the microphone's echo removed, ready for :func:`referat.merge.merge`.

    New :class:`referat.transcribe.ChannelTranscript` objects; the ones passed in
    are untouched, so the full segment lists stay available to whatever wrote
    `meta.json`. **The loopback transcript is returned exactly as it arrived** —
    there is no path through this function that can drop a loopback segment, which
    is the module's one invariant said in code.
    """
    if verdict.status != "suppressed":
        return transcripts
    echo = set(verdict.echo_clusters)
    out: list[ChannelTranscript] = []
    for transcript in transcripts:
        if transcript.channel != MIC_CHANNEL:
            out.append(transcript)
            continue
        kept = [
            segment
            for index, segment in enumerate(transcript.segments)
            if segment.speaker not in echo and index not in verdict.segments
        ]
        out.append(replace(transcript, segments=kept))
    return out


def mark_clusters(transcripts: list[ChannelTranscript], verdict: Suppression) -> list[str]:
    """Flag the convicted microphone clusters, and report which ones.

    Sets :attr:`referat.voices.Cluster.echo`, which is what keeps an echo cluster
    out of `referat label`'s queue and out of every other surface that asks
    :func:`referat.voices.unknown_speakers` who still needs a name. It runs here
    rather than during identification because identification happens per channel,
    mic first, and the loopback does not exist yet at that point.

    Mutates the clusters in place. They are the objects `meta.json` is about to be
    written from, and the flag has to be in the file — a verdict that lived only in
    memory would be re-derived by every reader, which is the inference this
    project's lifecycle field exists to delete.
    """
    marked: list[str] = []
    echo = set(verdict.echo_clusters)
    if not echo:
        return marked
    for transcript in transcripts:
        if transcript.channel != MIC_CHANNEL:
            continue
        for label, cluster in (transcript.speakers or {}).items():
            if label in echo:
                cluster.echo = True
                # The caller deletes the files; this is the record of them. Left
                # alone it would put paths in `meta.json` that no longer resolve,
                # which is the kind of small lie every later reader has to work
                # around once.
                cluster.snippets = []
                marked.append(label)
    return marked


def _channel(transcripts: list[ChannelTranscript], channel: str) -> ChannelTranscript | None:
    for transcript in transcripts:
        if transcript.channel == channel:
            return transcript
    return None


def describe(verdict: Suppression) -> None:
    """Log what suppression did, one line per convicted cluster.

    Nothing is dropped quietly. This mirrors the hotword cap, which logs the terms
    it had to leave out rather than leaving them out silently, and
    :func:`referat.voices._identify`, which reports the near-misses it refused.
    """
    if verdict.status == "skipped":
        log.info("no bleed suppression: %s", verdict.reason)
        return
    for label in verdict.echo_clusters:
        v = verdict.clusters[label]
        log.info(
            "bleed: %s%s is the loopback coming back - %d segment(s), %.0f%% of its "
            "time under loopback speech, %.0f%% of its lines matched",
            label,
            f" ({v.name})" if v.name else "",
            v.segments,
            v.time_covered * 100,
            v.text_matched * 100,
        )
    if verdict.segments:
        log.info(
            "bleed: %d further microphone segment(s) duplicate a loopback line",
            len(verdict.segments),
        )
    log.info(
        "bleed: dropped %d of %d microphone segment(s), %.0fs",
        verdict.dropped,
        verdict.dropped + verdict.kept,
        verdict.dropped_seconds,
    )
