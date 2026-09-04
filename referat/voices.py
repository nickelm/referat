r"""The known-voices database, and putting its names onto diarized clusters.

Step 7 gives each remote speaker a number; this module gives them a name. There
is **no enrollment step**, because every meeting already produces exactly what an
enrollment session would: `pyannote` computes one embedding per speaker cluster
in order to build the clustering at all, and hands it back as
`DiarizeOutput.speaker_embeddings`. Referat keeps it, cuts a few representative
snippets of that speaker to `speakers/`, and matches the embedding by cosine
similarity — the metric those embeddings are trained for — against a database of
embeddings grouped by name. Every meeting is therefore its own enrollment
session, and the next meeting knows more voices than the last.

**A wrong name is worse than no name.** A match is accepted only above
`match_threshold` *and* only when the winner beats the runner-up *name* by
`match_margin`. Anything less stays `SPEAKER_NN` and waits for `referat label`.
The near-miss is still written to `meta.json`, because that is the only material
there is for calibrating the two numbers.

**Nothing here may raise into the pipeline.** Identification degrades exactly as
diarization does — see :mod:`referat.diarize` — and for the same reason: an
exception escaping on the CUDA attempt would make
:func:`referat.transcribe.transcribe_meeting` re-transcribe the entire meeting on
the CPU, unable to tell an identification problem from a dying GPU. Any failure
at all costs names and nothing else.

**Imports stay light.** numpy and the standard library at module scope, nothing
more: `referat label` reads JSON, compares vectors and plays WAVs, and nobody
should need the `transcribe` extra's three gigabytes of torch resident to type a
name. `referat.diarize` is imported inside the one function that needs an
embedding model, and it is itself free of torch until called.

**The database is biometric personal data** about people who never asked to be in
it. It lives in `<meetings_dir>/.voices/` by default, or wherever
`[paths].voices_dir` points when the meetings folder is inside a sync client. It
never leaves the machine, is excluded from sync and backups, is kept away from
the `/cleanup` pass, and forgetting a person is a real deletion rather than a
tombstone.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import wave
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from referat import paths
from referat.config import Config

if TYPE_CHECKING:
    from referat.meeting import Meeting
    from referat.transcribe import ChannelTranscript

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

BACKUPS_DIR = "backups"
BACKUPS_KEPT = 15
"""How many previous versions of `voices.json` are kept beside it.

Fifteen because the file is small — about 190 KB with nine people in it, so the
whole history costs a few megabytes — and because the failure this exists for is
*silent*: a database replaced by a bad write is not noticed when it happens, it
is noticed weeks later when somebody Referat used to recognise comes back as
`SPEAKER_02`. A depth of one would be useless for that; a depth of fifteen spans
however many labelling sessions it takes to notice.

They live **inside** the voices folder on purpose, and that is the whole reason
this is safe to do at all. `.voices/` is kept out of the sync client by
`[paths].voices_dir` pointing somewhere local, so a copy made here inherits that
protection. Writing backups anywhere else — a temp folder, the meetings folder,
anywhere a sync client can see — would take biometric data of people who never
asked to be in a database and put it exactly where the whole design says it must
never go.
"""


class VoicesError(Exception):
    """A write to the known-voices database that must not happen.

    The only exception this module raises, and it is raised in exactly one
    situation: a save over a database that failed to parse. Everything else here
    degrades — a missing token, a missing file, an embedding pyannote did not
    return — because a diarization problem may cost speaker names and never a
    transcript. This one cannot degrade, because degrading *is* the data loss.
    """


def _back_up(path: Path) -> None:
    """Copy the current `voices.json` aside before it is overwritten. Never raises.

    Never raises because a failure to back up must not become a failure to
    *name somebody* — the operation somebody actually asked for is still valid,
    and refusing it because a copy could not be made would be the tail wagging
    the dog. It is logged instead, at warning level, which is where the evidence
    goes.

    A missing file is not an error and is the ordinary state before the first
    `referat label`; there is nothing to preserve.
    """
    if not path.exists():
        return
    folder = path.parent / BACKUPS_DIR
    try:
        folder.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        target = folder / f"{path.stem}-{stamp}{path.suffix}"
        # Copied byte for byte rather than re-serialized from this object: the
        # point is to preserve what is *on disk*, including a file this process
        # could not parse and is about to be stopped from overwriting anyway.
        target.write_bytes(path.read_bytes())
        _rotate(folder, path.stem)
    except OSError:
        log.warning("could not back up %s before overwriting it", path, exc_info=True)


def _rotate(folder: Path, stem: str) -> None:
    """Keep the newest :data:`BACKUPS_KEPT` copies and delete the rest.

    Sorted by name rather than by mtime, which works because the stamp is
    `%Y%m%d-%H%M%S` and so sorts chronologically as text — the same property
    meeting ids have, and relied on for the same reason: a file's mtime is a
    fact about the filesystem and can be changed by a copy, while the name is a
    fact about when the backup was taken.
    """
    copies = sorted(folder.glob(f"{stem}-*.json"))
    for stale in copies[:-BACKUPS_KEPT]:
        try:
            stale.unlink()
        except OSError:
            log.debug("could not remove the old backup %s", stale, exc_info=True)


def latest_backup(config: Config) -> Path | None:
    """The most recent backup of the database, or `None`. What a refusal points at."""
    folder = config.voices_dir() / BACKUPS_DIR
    copies = sorted(folder.glob(f"{Path(paths.VOICES_JSON).stem}-*.json"))
    return copies[-1] if copies else None

"""`voices.json`'s own version, so a later shape can be migrated rather than guessed."""

SPEAKER_RE = re.compile(r"^speaker_\d+$", re.IGNORECASE)
RESERVED_NAMES = ("ME", "REMOTE")
"""Names that would collide with the labels the pipeline generates by itself."""

MIN_SNIPPET_SECONDS = 1.0
"""Shorter than this is not worth writing: nobody can recognise a voice from it."""

DO_NOT_SYNC_TEXT = """# Do not sync this folder

`voices.json` holds voiceprints — numerical embeddings of the voices of everyone
who has been recorded in a meeting. It is biometric personal data about people
who never asked to be in a database, and most of them do not know it exists.

It must never leave this machine:

- exclude this folder from Dropbox, OneDrive, iCloud and every other sync client
- exclude it from every backup tool
- never copy it anywhere, and never paste any of it into a prompt

`referat label --forget <name>` deletes a person outright: their embeddings go,
and their name reverts to `SPEAKER_NN` in every transcript that carried it.
"""


# --- Names ------------------------------------------------------------------


def name_complaint(name: str) -> str | None:
    """What is wrong with `name` as a person's name, or None when nothing is.

    `ME` and `REMOTE` are the channel labels and `SPEAKER_NN` is what an
    unidentified speaker is already called, so all three would make the database
    say something the transcript already says differently.
    """
    cleaned = name.strip()
    if not cleaned:
        return "must not be empty"
    if cleaned.upper() in RESERVED_NAMES:
        return f"must not be {cleaned!r}: that is a channel label, not a person"
    if SPEAKER_RE.match(cleaned):
        return f"must not be {cleaned!r}: that is what an unnamed speaker is already called"
    if ":" in cleaned or "\n" in cleaned:
        return "must not contain ':' or a line break — it goes into a transcript label"
    return None


# --- The database -----------------------------------------------------------


@dataclass
class Voiceprint:
    """One embedding, stamped with where it came from.

    The provenance is the point: a cluster that turned out to hold two people
    produces an embedding of neither, and without knowing which meeting and which
    cluster it came from there is no way to find it again and pull it out.
    """

    embedding: np.ndarray
    meeting: str = ""
    speaker: str = ""
    added: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "embedding": [round(float(x), 6) for x in self.embedding],
            "meeting": self.meeting,
            "speaker": self.speaker,
            "added": self.added,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Voiceprint | None:
        """One stored entry, or None when it is not a usable vector."""
        values = raw.get("embedding")
        if not isinstance(values, list) or not values:
            return None
        try:
            embedding = np.asarray(values, dtype=np.float32)
        except (TypeError, ValueError):
            return None
        if embedding.ndim != 1 or not np.isfinite(embedding).all():
            return None
        return cls(
            embedding=embedding,
            meeting=str(raw.get("meeting", "")),
            speaker=str(raw.get("speaker", "")),
            added=str(raw.get("added", "")),
        )


@dataclass
class VoicesDB:
    """Names mapped to the embeddings that have been filed under them.

    A *list* per person, deliberately not an average: the same person on a
    different headset, a bad connection or a cold lands somewhere else in the
    space, and averaging those together would blur the one thing being matched on.
    """

    path: Path
    people: dict[str, list[Voiceprint]] = field(default_factory=dict)
    unreadable: bool = False
    """The file is there but would not parse, so this object is empty by accident.

    **The same flag `ProjectsDB` and `ActionsDB` carry, and the stakes here are
    the highest of the three.** An empty database and an unreadable one look
    identical from the outside: reading degrades to *nobody is known*, which is
    right, because a broken file may never cost a transcript. Writing is the
    opposite — :meth:`save` rewrites this file whole, so a save over a database
    that failed to parse replaces every voiceprint on this machine with whatever
    one entry happened to be added.

    That is not recoverable by retyping. A voiceprint is an embedding computed
    from audio that has since been deleted, and `.voices/` is deliberately
    excluded from sync and from backup, so there is no copy anywhere. The most
    dangerous caller is not a person at a prompt: :func:`bootstrap_owner` runs
    **inside the transcription pipeline**, loads, adds one embedding and saves,
    with nobody watching.

    A missing file is not unreadable — that is the ordinary state before the
    first `referat label`.
    """

    @classmethod
    def load(cls, config: Config) -> VoicesDB:
        """Read the database. Never raises — an unreadable one comes back empty.

        Failing here would cost the transcript, and the database is only ever
        worth speaker names.

        Takes the whole config rather than a folder so that every caller gets the
        same answer to where the voiceprints live, including when
        `[paths].voices_dir` moves them off the synced meetings folder.
        """
        path = config.voices_dir() / paths.VOICES_JSON
        db = cls(path=path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return db
        except (OSError, json.JSONDecodeError):
            log.warning("cannot read the known-voices database at %s", path, exc_info=True)
            db.unreadable = True
            return db
        if not isinstance(raw, dict):
            log.warning("ignoring malformed %s", path)
            db.unreadable = True
            return db

        for name, entries in (raw.get("people") or {}).items():
            if not isinstance(entries, list):
                continue
            prints = [
                p
                for entry in entries
                if isinstance(entry, dict) and (p := Voiceprint.from_json(entry)) is not None
            ]
            if prints:
                db.people[str(name)] = prints
        log.debug("loaded %d known voice(s) from %s", len(db.people), path)
        return db

    def save(self) -> None:
        """Write the database atomically, keeping a copy of what was there before.

        **Refuses outright when :attr:`unreadable`**, rather than trusting every
        caller to have checked. Callers do check — that is where the message a
        person reads comes from — but this is the one file in Referat that cannot
        be reconstructed by hand if a check is ever forgotten, so the refusal
        lives at the write as well. :class:`VoicesError` is deliberately loud:
        there is no sensible way to carry on, and carrying on is the failure.

        The backup is taken **before** the write, from the bytes actually on
        disk, and is the answer to the fact that `.voices/` is excluded from
        sync and from backup on purpose. That exclusion protects people who
        never asked to be in a database; it also means a bad write has nothing
        to restore from, and `paths.write_json_atomic` protects against a *torn*
        write and not against a wrong one.
        """
        if self.unreadable:
            raise VoicesError(
                f"{self.path} exists but could not be read, so writing now would replace "
                f"every voiceprint on this machine with what is in memory. Fix or move "
                f"that file first; a copy of the last good one may be in "
                f"{self.path.parent / BACKUPS_DIR}."
            )
        ensure_voices_dir(self.path.parent)
        _back_up(self.path)
        payload: dict[str, object] = {
            "version": SCHEMA_VERSION,
            "people": {
                name: [p.to_json() for p in prints]
                for name, prints in sorted(self.people.items())
            },
        }
        paths.write_json_atomic(self.path, payload)

    def names(self) -> list[str]:
        return sorted(self.people)

    def add(self, name: str, embedding: np.ndarray, meeting: str, speaker: str) -> None:
        """File one embedding under `name`. Does not save; the caller decides when."""
        self.people.setdefault(name, []).append(
            Voiceprint(
                embedding=np.asarray(embedding, dtype=np.float32),
                meeting=meeting,
                speaker=speaker,
                added=dt.date.today().isoformat(),
            )
        )

    def forget(self, name: str) -> int:
        """Delete a person outright and report how many embeddings went with them."""
        return len(self.people.pop(name, []))

    def drop(self, meeting: str, speaker: str) -> dict[str, int]:
        """Remove the voiceprints filed from one cluster of one meeting.

        Returns how many went, per name. This is what :class:`Voiceprint`'s
        provenance is *for* — see its docstring, which says a cluster that turned
        out to hold the wrong thing produces an embedding of nobody, and that
        without knowing which meeting and which cluster it came from there is no
        way to find it again and pull it out. The verb simply had not been written
        until an echo cluster put two of them in here under real names.

        Addressed by where the print came from rather than by whom it is filed
        under, because that is the fact you actually know: the label was *correct*,
        and the recording behind it was not.

        **Refuses to leave a name with no prints at all.** A person with nothing on
        file is a person who has been forgotten, and forgetting somebody is
        :func:`referat.label.forget`'s job — it also reverts their transcript
        labels, which this must not do, because those labels are right.
        """
        removed: dict[str, int] = {}
        for name, prints in list(self.people.items()):
            keep = [p for p in prints if not (p.meeting == meeting and p.speaker == speaker)]
            if len(keep) == len(prints):
                continue
            if not keep:
                raise ValueError(
                    f"{name} has no other voiceprint, so this would delete them "
                    f"entirely; `referat label --forget {name}` is what does that"
                )
            removed[name] = len(prints) - len(keep)
            self.people[name] = keep
        return removed


def ensure_voices_dir(folder: Path) -> Path:
    """Create the known-voices folder and seed its `DO-NOT-SYNC.md`.

    Takes the folder itself — resolve it with
    :meth:`referat.config.Config.voices_dir`. It used to accept either that or
    the meetings folder and tell them apart by name, which stopped being safe the
    moment `[paths].voices_dir` could point at a folder not called `.voices`.
    """
    folder.mkdir(parents=True, exist_ok=True)
    warning = folder / paths.DO_NOT_SYNC_MD
    if not warning.exists():
        paths.write_text_atomic(warning, DO_NOT_SYNC_TEXT)
        log.info("seeded %s", warning)
    return folder


# --- Matching ---------------------------------------------------------------


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity, -1.0 to 1.0. Zero for a zero-length or mismatched vector.

    This is the metric pyannote's own clustering compares embeddings to centroids
    with, so it is the metric a centroid should be compared to a stored centroid
    with too.
    """
    if a.shape != b.shape:
        return 0.0
    denominator = float(np.linalg.norm(a)) * float(np.linalg.norm(b))
    if denominator <= 0.0:
        return 0.0
    return float(np.dot(a, b) / denominator)


@dataclass(frozen=True)
class Match:
    """The best name for one cluster, and whether it was good enough to use."""

    name: str
    score: float
    runner_up: float
    """The best score of the next *name* down, or -1.0 when there was only one."""
    accepted: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "score": round(self.score, 4),
            "runner_up": round(self.runner_up, 4),
            "accepted": self.accepted,
        }


def match(embedding: np.ndarray, db: VoicesDB, threshold: float, margin: float) -> Match | None:
    """The closest known voice to `embedding`, or None when the database is empty.

    Each name is scored by its *best* stored embedding, not its average: a person
    with twenty voiceprints has twenty chances to be recognised, and a single bad
    one cannot drag the other nineteen down.

    A returned :class:`Match` may well have `accepted` False. That is deliberate —
    the near-misses are the only material there is for calibrating the two
    thresholds, so they are recorded in `meta.json` rather than thrown away.

    **A database holding one name is the dangerous case, not the easy one.** The
    margin exists to refuse a voice that two people fit about equally well, and
    with a single name there is no runner-up to measure against — so the margin
    would be trivially satisfied and the threshold left deciding alone. That is
    exactly the state the database is in just after `owner_name` is first set,
    which is also when a false accept costs the most: somebody else's words
    rendered under the owner's own name. Verified rather than reasoned about — a
    different synthetic voice scored 0.7404 against a lone voiceprint and was
    accepted at the 0.70 threshold. With no runner-up the margin is therefore required of the
    *score* instead, so the bar is never lower for having less to compare against.
    """
    scored = [
        (max(cosine(embedding, p.embedding) for p in prints), name)
        for name, prints in db.people.items()
        if prints
    ]
    if not scored:
        return None
    # Sorted by score alone, then by name, so equal scores resolve the same way
    # every run rather than by dict order.
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    score, name = scored[0]
    if len(scored) > 1:
        runner_up = scored[1][0]
        clear = (score - runner_up) >= margin
    else:
        # No second name to beat, so the margin is asked of the score itself.
        runner_up = -1.0
        clear = score >= threshold + margin
    return Match(
        name=name,
        score=score,
        runner_up=runner_up,
        accepted=score >= threshold and clear,
    )


# --- Snippets ---------------------------------------------------------------


def pick_snippets(
    spans: Sequence[tuple[float, float]], count: int, seconds: float
) -> list[tuple[float, float]]:
    """Up to `count` clips of at most `seconds`, taken from the longest spans.

    Longest first because a long uninterrupted turn is the one most likely to be
    that speaker alone, and centred within it because the ends of a turn are
    where a diarizer's boundaries are least certain. The result is returned in
    meeting order, so the files number `_1`, `_2`, `_3` down the transcript.
    """
    usable = [(s, e) for s, e in spans if e - s >= MIN_SNIPPET_SECONDS]
    longest = sorted(usable, key=lambda span: span[1] - span[0], reverse=True)[:count]
    clipped = []
    for start, end in longest:
        middle = (start + end) / 2.0
        half = min(seconds, end - start) / 2.0
        clipped.append((middle - half, middle + half))
    return sorted(clipped)


def write_snippet(path: Path, audio: np.ndarray, sample_rate: int) -> None:
    """Write mono float32 audio as the 16-bit PCM WAV the recorder also produces."""
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = np.clip(audio, -1.0, 1.0) * 32767.0
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(samples.astype(np.int16).tobytes())


def cut_snippets(
    audio: np.ndarray,
    sample_rate: int,
    dest: Path,
    speaker: str,
    spans: Sequence[tuple[float, float]],
    count: int,
    seconds: float,
) -> list[dict[str, Any]]:
    """Cut a speaker's representative clips to `dest`, returning what was written.

    Cut **during the pipeline**, not on demand: the WAVs they come from are
    deleted the moment the meeting transcribes cleanly, so offsets alone would
    point at nothing by the time anyone ran `referat label`.
    """
    written: list[dict[str, Any]] = []
    for index, (start, end) in enumerate(pick_snippets(spans, count, seconds), start=1):
        first = max(0, int(start * sample_rate))
        last = min(audio.size, int(end * sample_rate))
        if last - first < int(MIN_SNIPPET_SECONDS * sample_rate):
            continue
        path = dest / f"{speaker}_{index}.wav"
        write_snippet(path, audio[first:last], sample_rate)
        written.append({"file": path.name, "start": round(start, 3), "end": round(end, 3)})
    return written


def snippet_paths(meeting: Meeting, speaker: str) -> list[Path]:
    """Every snippet on disk for one speaker, in order. Empty when there are none."""
    folder = meeting.speakers_dir
    if not folder.is_dir():
        return []
    return sorted(folder.glob(f"{speaker}_*.wav"))


def drop_snippets(meeting: Meeting, speaker: str) -> int:
    """Delete a speaker's snippets once they have a name. Never raises."""
    removed = 0
    for path in snippet_paths(meeting, speaker):
        try:
            path.unlink()
            removed += 1
        except OSError:
            log.warning("could not delete %s", path, exc_info=True)
    folder = meeting.speakers_dir
    try:
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    except OSError:
        pass
    return removed


# --- One speaker of one channel ---------------------------------------------


@dataclass
class Cluster:
    """What is known about one diarized speaker, for the `speakers` block of `meta.json`."""

    label: str
    embedding: np.ndarray | None = None
    snippets: list[dict[str, Any]] = field(default_factory=list)
    name: str = ""
    """The name this speaker was resolved to, or empty while they are a number."""
    match: Match | None = None
    """The best candidate, accepted or not. Kept for calibrating the thresholds."""
    echo: bool = False
    """Whether :mod:`referat.bleed` found this to be the loopback coming back.

    Set on microphone clusters only, and only after both channels are
    transcribed — see :func:`referat.bleed.mark_clusters`. An echo cluster has no
    lines left in `transcript.md`, so it must never be offered a name:
    :func:`unknown_speakers` skips it and :func:`referat.label.apply_name`
    refuses it. That is the whole defence, and it is here rather than in
    :func:`identify` because the loopback does not exist yet when identification
    runs.

    The embedding is kept anyway. It is the record of what a room microphone
    heard through a loudspeaker and the only material that will calibrate the
    suppression thresholds, and it is inert now that the one thing that reads it
    refuses.
    """

    def to_json(self) -> dict[str, Any]:
        meta: dict[str, Any] = {"snippets": self.snippets}
        if self.embedding is not None:
            meta["embedding"] = [round(float(x), 6) for x in self.embedding]
        if self.name:
            meta["name"] = self.name
        if self.echo:
            meta["echo"] = True
        if self.match is not None:
            meta["match"] = self.match.to_json()
        return meta


# --- The pipeline pass ------------------------------------------------------


def identify(
    meeting: Meeting,
    transcript: ChannelTranscript,
    renaming: dict[str, str],
    audio: np.ndarray,
    sample_rate: int,
    config: Config,
) -> dict[str, str]:
    """Name what can be named on one channel, and cut snippets for what cannot.

    Returns the `SPEAKER_NN -> name` mapping that was accepted, and fills
    `transcript.speakers` with one :class:`Cluster` per speaker on the way.

    `renaming` is :func:`referat.diarize.assign`'s map from pyannote's own labels
    to the `SPEAKER_NN` the transcript uses. Carrying the embeddings through it is
    the whole trick: pyannote orders them by *its* labels, `assign` renumbers by
    first appearance, and permuting one without the other files every voice under
    somebody else.

    **Never raises.** Anything at all going wrong leaves the speakers as numbers,
    which is what they already were.
    """
    result = transcript.diarization
    if result is None or not result.ok or not renaming:
        return {}
    if not config.speakers.identify:
        log.info("%s: speaker identification is off in the config", transcript.channel)
        transcript.speakers = {label: Cluster(label=label) for label in sorted(renaming.values())}
        return {}

    try:
        return _identify(meeting, transcript, renaming, audio, sample_rate, config)
    except Exception:
        # Deliberately bare, exactly as `diarize.diarize` is. A database that will
        # not load, an embedding of the wrong width, a disk that will not take the
        # snippets: all of them cost names and stop there.
        log.exception("speaker identification failed on %s", transcript.channel)
        transcript.speakers = {label: Cluster(label=label) for label in sorted(renaming.values())}
        return {}


def _identify(
    meeting: Meeting,
    transcript: ChannelTranscript,
    renaming: dict[str, str],
    audio: np.ndarray,
    sample_rate: int,
    config: Config,
) -> dict[str, str]:
    """The body of :func:`identify`, free to raise into its handler."""
    settings = config.speakers
    result = transcript.diarization
    assert result is not None

    spans: dict[str, list[tuple[float, float]]] = {}
    for turn in result.turns:
        label = renaming.get(turn.speaker)
        if label is not None:
            spans.setdefault(label, []).append((turn.start, turn.end))

    db = VoicesDB.load(config)
    clusters: dict[str, Cluster] = {}
    names: dict[str, str] = {}
    for raw, label in sorted(renaming.items(), key=lambda item: item[1]):
        embedding = result.embeddings.get(raw)
        cluster = Cluster(label=label, embedding=embedding)
        if embedding is not None:
            cluster.match = match(embedding, db, settings.match_threshold, settings.match_margin)
            if cluster.match is not None and cluster.match.accepted:
                cluster.name = cluster.match.name
                names[label] = cluster.match.name
        if not cluster.name:
            # Only the ones nobody can name yet need audio kept for them.
            cluster.snippets = cut_snippets(
                audio,
                sample_rate,
                meeting.speakers_dir,
                label,
                spans.get(label, []),
                settings.snippets_per_speaker,
                settings.snippet_seconds,
            )
        clusters[label] = cluster

    transcript.speakers = clusters
    unknown = [label for label in clusters if label not in names]
    log.info(
        "%s: identified %s; %s still unknown",
        transcript.channel,
        ", ".join(f"{k} = {v}" for k, v in sorted(names.items())) or "nobody",
        ", ".join(sorted(unknown)) or "nobody",
    )
    for label in unknown:
        candidate = clusters[label].match
        if candidate is not None:
            log.info(
                "%s: %s looks most like %s at %.3f (runner-up %.3f), short of %.2f/%.2f",
                transcript.channel,
                label,
                candidate.name,
                candidate.score,
                candidate.runner_up,
                settings.match_threshold,
                settings.match_margin,
            )
    return names


def unknown_speakers(meeting: Meeting) -> list[str]:
    """Every speaker in the meeting that still has no name, in label order.

    Read out of `meta.json` rather than off the transcript, so the tray
    notification, `referat label` and step 11's tree all agree on the count.

    **An echo cluster is not offered.** It is the loopback coming back into the
    room microphone, its lines are not in `transcript.md` at all, and asking
    somebody to name it is asking them to file a voiceprint recorded off a
    loudspeaker under a real person's name — which is exactly how two of them got
    into the database on 2026-09-02. One filter here covers `referat label`, the
    command center's speaker dialog, `referat list`'s unnamed column, the tray's
    post-transcription notification and `label.run_json`'s gallery, because all
    five ask this function.
    """
    channels = (meeting.transcription.get("channels") or {}).values()
    labels = {
        label
        for channel in channels
        if isinstance(channel, dict)
        for label, cluster in (channel.get("speakers") or {}).items()
        if not (isinstance(cluster, dict) and cluster.get("echo"))
    }
    return sorted(labels - set(meeting.speaker_names))


def speaker_channel(meeting: Meeting, speaker: str) -> str:
    """Which channel a label came out of - `mic`, `system`, or `""` when unknown.

    Worth having because the answer is a strong hint about *who* a speaker is,
    and because nothing was showing it. The person naming speakers was asked for
    four names after one Teams call and one of them was themself, with nothing on
    screen saying that cluster came out of their own microphone.

    A hint and never a name: the mic hears the whole room in a meeting held in
    person, so which channel a voice arrived on narrows the guess rather than
    settling it, and *a wrong name is worse than no name* is unchanged by it.
    """
    for name, channel in (meeting.transcription.get("channels") or {}).items():
        if isinstance(channel, dict) and speaker in (channel.get("speakers") or {}):
            return str(name)
    return ""


def channel_speakers(meeting: Meeting, channel: str) -> list[str]:
    """Every label one channel clustered into, in label order.

    The counterpart of :func:`speaker_channel`, and the question `referat relabel`
    asks: a meeting whose *microphone* clustered into nothing but the owner is one
    where the mic held one person, which is what makes its `ME` lines safe to
    spell out.
    """
    entry = (meeting.transcription.get("channels") or {}).get(channel)
    if not isinstance(entry, dict):
        return []
    return sorted(entry.get("speakers") or {})


def is_echo(meeting: Meeting, speaker: str) -> bool:
    """Whether `meta.json` records this cluster as the loopback coming back.

    The question :func:`referat.label.apply_name` asks before filing a voiceprint.
    Read off the stored record rather than recomputed, because the verdict was
    reached with both channels' segments in hand and neither survives the pipeline
    — the same reason the lifecycle is a value the pipeline writes rather than a
    state every reader infers.
    """
    cluster = _stored_cluster(meeting, speaker)
    return bool(cluster and cluster.get("echo"))


def _stored_cluster(meeting: Meeting, speaker: str) -> dict[str, Any] | None:
    """The raw `speakers` entry for one label in `meta.json`, or None."""
    for channel in (meeting.transcription.get("channels") or {}).values():
        if not isinstance(channel, dict):
            continue
        entry = (channel.get("speakers") or {}).get(speaker)
        if isinstance(entry, dict):
            return entry
    return None


def set_stored_name(meeting: Meeting, speaker: str, name: str) -> None:
    """Record a name in the channel's `speakers` block. Does not save.

    `speaker_names` is the authority on who a label is; this keeps the per-channel
    record from contradicting it.
    """
    entry = _stored_cluster(meeting, speaker)
    if entry is not None:
        entry["name"] = name


def clear_stored_name(meeting: Meeting, speaker: str) -> None:
    """Strip every trace of a name from a channel's `speakers` block. Does not save.

    Both the `name` and the `match` that produced it: the match block records the
    winning *name* along with its score, so leaving it would leave the person
    behind in a file that is supposed to have forgotten them. The embedding stays
    — it is the voice, not the person, and it is what makes the label nameable
    again.
    """
    entry = _stored_cluster(meeting, speaker)
    if entry is not None:
        entry.pop("name", None)
        entry.pop("match", None)


def stored_embedding(meeting: Meeting, speaker: str) -> np.ndarray | None:
    """The embedding `meta.json` kept for one speaker, or None when there is none.

    This is what makes `referat label` cheap: the vector was computed during the
    pipeline and written down, so naming a speaker afterwards costs a JSON read
    rather than three gigabytes of torch and a second pass over the audio.
    """
    for channel in (meeting.transcription.get("channels") or {}).values():
        if not isinstance(channel, dict):
            continue
        entry = (channel.get("speakers") or {}).get(speaker)
        if isinstance(entry, dict) and isinstance(entry.get("embedding"), list):
            try:
                values = np.asarray(entry["embedding"], dtype=np.float32)
            except (TypeError, ValueError):
                return None
            return values if values.ndim == 1 and values.size else None
    return None


# --- Bootstrapping the owner ------------------------------------------------


def bootstrap_owner(
    meeting: Meeting,
    transcripts: Iterable[ChannelTranscript],
    config: Config,
) -> bool:
    """File the owner's voiceprint from a meeting where the mic held one voice.

    Never raises. Returns whether anything was added.

    **The gate is now measured rather than inferred.** It used to be that the
    loopback channel had to hold voice: on a Zoom call the remote speakers arrive
    through the loopback, so the microphone was taken to be the owner and nobody
    else, while a meeting held in person put the whole room through the mic and
    an embedding of that would be a blend of everybody.

    That reasoning was sound and its condition was the wrong one — it can only
    ever fire on remote calls, which are the *exception* here, so for the meetings
    this is actually used for the owner voiceprint would never have been created
    at all. Now that the microphone is diarized like the loopback is, the question
    has a direct answer: **the mic is unambiguously one person when it clustered
    into exactly one speaker.** That is true of a solo recording and of a remote
    call, false of a room with two people in it, and it also refuses the
    speakerphone case the old gate was most exposed to — voices echoing back off
    a desk speakerphone cluster as a second speaker rather than quietly poisoning
    the owner's own voiceprint.

    Refusing when the mic did not diarize at all is deliberate. With diarization
    off there is nothing to count, so there is no evidence the microphone held one
    person, and a guess here writes biometric data under somebody's name.

    The embedding is the cluster's own centroid, which diarization already
    computed. Nothing is re-embedded, so this no longer costs a second pipeline
    load on the meetings that qualify.
    """
    settings = config.speakers
    owner = settings.owner_name.strip()
    if not settings.identify or not owner:
        return False
    try:
        return _bootstrap_owner(meeting, list(transcripts), config, owner)
    except Exception:
        log.exception("could not add an owner voiceprint for %s", meeting.id)
        return False


def _bootstrap_owner(
    meeting: Meeting,
    transcripts: list[ChannelTranscript],
    config: Config,
    owner: str,
) -> bool:
    """The body of :func:`bootstrap_owner`, free to raise into its handler."""
    mic = next((t for t in transcripts if t.channel == "mic"), None)
    if mic is None or not mic.segments:
        log.debug("no mic transcript in %s; not adding an owner voiceprint", meeting.id)
        return False

    clusters = mic.speakers or {}
    if len(clusters) != 1:
        log.info(
            "%s: the microphone holds %d voices, so it is not unambiguously %s; "
            "not adding an owner voiceprint",
            meeting.id,
            len(clusters),
            owner,
        )
        return False

    cluster = next(iter(clusters.values()))
    if cluster.embedding is None:
        log.debug("%s: the mic cluster has no embedding; not adding one", meeting.id)
        return False
    if cluster.name and cluster.name != owner:
        # Already recognised as somebody else. Believe the match over the config:
        # filing their voice under the owner's name is the one outcome worth
        # refusing outright.
        log.warning(
            "%s: the microphone was identified as %s, not %s; not adding an owner voiceprint",
            meeting.id,
            cluster.name,
            owner,
        )
        return False

    db = VoicesDB.load(config)
    if db.unreadable:
        # **The most dangerous caller in the codebase, and the reason the flag
        # above exists.** This runs on a transcription thread with nobody
        # watching: an unreadable database loads as *nobody is known*, and the
        # save two lines down would replace every voiceprint on this machine
        # with this one embedding. Nothing here is worth that -- an owner
        # voiceprint is a convenience the next meeting would have created
        # anyway. Refused, loudly, and the transcript is unaffected.
        log.error(
            "%s: not adding an owner voiceprint -- %s could not be read, and saving "
            "over it would replace every voiceprint on this machine",
            meeting.id,
            db.path,
        )
        return False
    db.add(owner, cluster.embedding, meeting.id, cluster.label)
    db.save()
    log.info("added an owner voiceprint for %s from %s of %s", owner, cluster.label, meeting.id)
    return True
