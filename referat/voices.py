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

from referat import paths, projects
from referat.config import Config

if TYPE_CHECKING:
    from referat.meeting import Meeting
    from referat.transcribe import ChannelTranscript

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2
"""`voices.json`'s own version, so a later shape can be migrated rather than guessed.

**2 since build step 20c**: a person is a record keyed by an id, where version 1
keyed a list of prints by a bare name. Moved because the key changed *meaning*
— the same rule `projects.py` states about its own version — and because the
one lossy direction matters here more than anywhere: a version-1 reader handed
a version-2 file finds no list under any key and loads **nobody**, silently. A
version-1 file is read by this code without being rewritten; see
:meth:`VoicesDB.load`.
"""

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


def email_complaint(email: str) -> str | None:
    """What is wrong with `email` as an address, or None. Empty is fine.

    Deliberately shallow — one `@` with something on both sides and no
    whitespace. The field is **read by a person and sent nowhere**: Referat has
    no mail path and must not grow one, so the only thing worth refusing is a
    value that plainly is not an address, which is what a typo into the wrong
    field looks like.
    """
    cleaned = email.strip()
    if not cleaned:
        return None
    if any(c.isspace() for c in cleaned):
        return "must not contain whitespace"
    user, at, host = cleaned.partition("@")
    if not at or not user or "." not in host or host.startswith(".") or host.endswith("."):
        return f"does not look like an address: {cleaned!r}"
    return None


def person_id(value: str) -> str:
    """The id a stored name stands for: a legacy `speaker_names` value, or an id already.

    Before build step 20c `voices.json` was keyed by a bare name and
    `speaker_names` stored that name; since it, both hold an id. The two are
    told apart by not telling them apart: an id is :func:`referat.projects.slugify`
    of the name it was made from, and `slugify` is idempotent on its own output,
    so `Lars Klein`, `lars-klein` and `LARS-KLEIN` all resolve to `lars-klein`.
    That is what lets every file written before the change be read without
    being rewritten — the same move `MeetingStatus` makes for `stopped` and
    `done`, and for the same reason.

    The one case it cannot serve is a person created **after** a namesake, whose
    id carries a `-2`: a legacy value never names them, because they did not
    exist when it was written, so the plain slug is the right answer for it.
    """
    return projects.slugify(value) or value


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
class Person:
    """One person on file: an identity, two spellings, an address, and their prints.

    **The id is the identity and the names are display fields**, which is
    exactly what a project already is in `projects.json` and for the same
    reason: a rename touches one record, and no `meta.json`, no transcript and
    no doc is disturbed by it. Two people who share a full name are two ids —
    `john-smith` and `john-smith-2` — which is the answer to the question of
    whether names must be unique: they need not be, because they are not the key.

    `name` is the full name and `short` is what the transcript renders, which is
    the one place a person's name is prose rather than a record. Two people may
    share a short name; a transcript saying `Anna:` in two different meetings is
    no worse than it was before there were full names, and `referat people` shows
    both in full. `email` is **stored and never sent** — Referat has no mail path
    and must not grow one; the field is for a person reading the page, and
    :func:`referat.hotwords.collect` must never see it.

    A *list* of prints per person, deliberately not an average: the same person
    on a different headset, a bad connection or a cold lands somewhere else in
    the space, and averaging those together would blur the one thing being
    matched on.
    """

    id: str
    name: str
    short: str = ""
    email: str = ""
    prints: list[Voiceprint] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.name = self.name.strip()
        self.short = self.short.strip() or self.name
        self.email = self.email.strip()

    def record(self) -> dict[str, str]:
        """The four display fields, for a document. Never the prints."""
        return {"id": self.id, "name": self.name, "short": self.short, "email": self.email}

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "short": self.short,
            "email": self.email,
            "prints": [p.to_json() for p in self.prints],
        }

    @classmethod
    def from_json(cls, pid: str, raw: dict[str, Any]) -> Person:
        prints = [
            p
            for entry in (raw.get("prints") or [])
            if isinstance(entry, dict) and (p := Voiceprint.from_json(entry)) is not None
        ]
        return cls(
            id=pid,
            name=str(raw.get("name") or pid),
            short=str(raw.get("short") or ""),
            email=str(raw.get("email") or ""),
            prints=prints,
        )


@dataclass
class VoicesDB:
    """People keyed by id, each with the embeddings that have been filed under them.

    Keyed by **id** since build step 20c and by bare name before it; the file is
    read in either shape and written in the new one. See :class:`Person` for why
    the key is neither name.
    """

    path: Path
    people: dict[str, Person] = field(default_factory=dict)
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

        for key, value in (raw.get("people") or {}).items():
            if isinstance(value, dict):
                # Version 2: a record under an id.
                person = Person.from_json(str(key), value)
            elif isinstance(value, list):
                # Version 1: a list of prints under a bare name. Read as a
                # record whose id is the slug of that name and whose full and
                # short names are both the name, exactly as `MeetingStatus`
                # maps `stopped` and `done` on load. **The file is not
                # rewritten** by reading it; the next save writes version 2.
                prints = [
                    p
                    for entry in value
                    if isinstance(entry, dict) and (p := Voiceprint.from_json(entry)) is not None
                ]
                if not prints:
                    continue
                person = Person(id=person_id(str(key)), name=str(key), prints=prints)
            else:
                continue
            if person.id in db.people:
                # Two legacy names that slugify alike — `Anna` and `anna` — would
                # be two people merged into one voiceprint set on load, which is
                # the single worst failure this database has and the one build
                # step 20c exists to prevent. Refused as unreadable: reading still
                # degrades to *nobody is known*, and no write can land on it.
                log.error(
                    "%s: %r and another entry both resolve to the id %r; "
                    "refusing to read a database that would merge two people",
                    path,
                    key,
                    person.id,
                )
                db.people.clear()
                db.unreadable = True
                return db
            db.people[person.id] = person
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
            "people": {pid: person.to_json() for pid, person in sorted(self.people.items())},
        }
        paths.write_json_atomic(self.path, payload)

    # --- Reading ------------------------------------------------------------

    def ordered(self) -> list[Person]:
        """Everybody, by full name and then by id, so namesakes sit together."""
        return sorted(self.people.values(), key=lambda p: (p.name.casefold(), p.id))

    def ids(self) -> list[str]:
        """Every id, in :meth:`ordered` order."""
        return [person.id for person in self.ordered()]

    def names(self) -> list[str]:
        """Every full name, sorted. A display list; the gallery and the match use ids."""
        return sorted((person.name for person in self.people.values()), key=str.casefold)

    def spellings(self) -> list[str]:
        """Every way a person is written — full name and short name — and never the email.

        What :func:`referat.hotwords.collect` reads. Whisper may hear either
        spelling, so both go on the list when they differ; an address is not a
        word anybody says and must not be told to a speech model.
        """
        out: list[str] = []
        for person in self.ordered():
            for term in (person.name, person.short):
                if term and term not in out:
                    out.append(term)
        return out

    def get(self, pid: str) -> Person | None:
        return self.people.get(pid)

    def display(self, value: str) -> str:
        """The full name behind a stored id or legacy name, or the value itself.

        The value itself when nobody is filed under it, which is what drift
        looks like — a `speaker_names` entry whose person has since been
        forgotten — and is shown rather than hidden for the reason the people
        page gives.
        """
        person = self.people.get(person_id(value))
        return person.name if person is not None else value

    def label_for(self, value: str) -> str:
        """What the transcript calls the person behind a stored id or legacy name.

        The short name, or the value itself when nobody is filed under it.
        """
        person = self.people.get(person_id(value))
        return person.short if person is not None else value

    def resolve(self, text: str) -> tuple[Person | None, str]:
        """The person `text` means, or `(None, "")` for nobody, or `(None, why)`.

        In order: an id, a full name, a short name — each compared exactly and
        then case-insensitively. A full or short name two people share is
        **ambiguous and refused in words that list the ids**, rather than picking
        the first: choosing between two Annas on somebody's behalf is filing a
        voice under the wrong person, which is the failure this record exists to
        prevent. A number typed at the prompt never reaches this; the prompt
        turns it into an id first.
        """
        wanted = text.strip()
        if not wanted:
            return None, ""
        if (person := self.people.get(wanted)) is not None:
            return person, ""
        folded = wanted.casefold()
        for pick in (
            lambda p: p.name == wanted,
            lambda p: p.name.casefold() == folded,
            lambda p: p.short == wanted,
            lambda p: p.short.casefold() == folded,
        ):
            hits = [p for p in self.ordered() if pick(p)]
            if len(hits) == 1:
                return hits[0], ""
            if hits:
                ids = ", ".join(p.id for p in hits)
                return None, f"{wanted} could be any of {ids}; say which"
        return None, ""

    def owner(self, config: Config) -> Person | None:
        """The person `[speakers].owner_name` names, or None when nobody does yet.

        None also for an ambiguous owner name — logged, because the owner is the
        one person every meeting is scoped to, and an owner nothing can resolve
        is a configuration to fix rather than a guess to make.
        """
        wanted = config.speakers.owner_name.strip()
        if not wanted:
            return None
        person, why = self.resolve(wanted)
        if why:
            log.warning("[speakers].owner_name: %s", why)
        return person

    # --- Writing ------------------------------------------------------------

    def new_person(self, name: str, short: str = "", email: str = "") -> Person:
        """Create a record with a fresh id. Does not save; the caller decides when.

        The id is :func:`referat.projects.slugify` of the full name plus the
        same `-2` collision suffix a project gets, which is a rule this codebase
        has exactly one implementation of. The caller has already run
        :func:`name_complaint` over the names; this does not repeat it.
        """
        pid = projects.free_id(projects.slugify(name) or "person", self.people)
        person = Person(id=pid, name=name, short=short, email=email)
        self.people[pid] = person
        return person

    def add(self, pid: str, embedding: np.ndarray, meeting: str, speaker: str) -> None:
        """File one embedding under the person `pid`. Does not save.

        The person must exist — see :meth:`new_person` — because a print filed
        under an id nobody is recorded against would be a voice with no name.
        """
        self.people[pid].prints.append(
            Voiceprint(
                embedding=np.asarray(embedding, dtype=np.float32),
                meeting=meeting,
                speaker=speaker,
                added=dt.date.today().isoformat(),
            )
        )

    def forget(self, pid: str) -> int:
        """Delete a person outright and report how many embeddings went with them."""
        person = self.people.pop(pid, None)
        return len(person.prints) if person is not None else 0

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
        for pid, person in list(self.people.items()):
            prints = person.prints
            keep = [p for p in prints if not (p.meeting == meeting and p.speaker == speaker)]
            if len(keep) == len(prints):
                continue
            if not keep:
                raise ValueError(
                    f"{person.name} has no other voiceprint, so this would delete them "
                    f"entirely; `referat label --forget {pid}` is what does that"
                )
            removed[pid] = len(prints) - len(keep)
            person.prints = keep
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
    """The best person for one cluster, and whether it was good enough to use.

    `name` holds the person's **id** since build step 20c, and keeps its key in
    `meta.json` because every `match` block already written has one and a
    reader that renders it resolves it through :meth:`VoicesDB.display` either
    way — a legacy name and an id go through the same :func:`person_id`.
    """

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
        (max(cosine(embedding, p.embedding) for p in person.prints), pid)
        for pid, person in db.people.items()
        if person.prints
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
    noise: bool = False
    """Whether a person said this cluster is not a person — see :mod:`referat.noise`.

    A second verdict beside :attr:`echo` rather than a widening of it, because
    the two are reached differently and a later reader must be able to tell
    which: `echo` is measured against the other channel, `noise` is a human
    judgement made by listening, and nothing in the data checks it. They share
    the defence — :func:`unknown_speakers` skips both and
    :func:`referat.label.apply_name` refuses both — and nothing else. Never set
    by the pipeline; only :func:`mark_stored_noise` writes it, and only at a
    person's request.
    """

    def to_json(self) -> dict[str, Any]:
        meta: dict[str, Any] = {"snippets": self.snippets}
        if self.embedding is not None:
            meta["embedding"] = [round(float(x), 6) for x in self.embedding]
        if self.name:
            meta["name"] = self.name
        if self.echo:
            meta["echo"] = True
        if self.noise:
            meta["noise"] = True
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

    Returns the `SPEAKER_NN -> short name` mapping that was accepted — what the
    transcript renders — and fills `transcript.speakers` with one
    :class:`Cluster` per speaker on the way, each carrying the person's **id**
    in `Cluster.name`, which is what `speaker_names` records.

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
                # The cluster records the *id*, which is what `speaker_names`
                # stores; the transcript gets the short name, which is prose.
                cluster.name = cluster.match.name
                names[label] = db.label_for(cluster.match.name)
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

    **Nor is a noise cluster.** A person listened and said it is not a person —
    a door, the corridor, the meeting next door — and its lines are out of the
    transcript by :func:`referat.noise.mark_noise`. It is the way out of the
    *Speakers nobody has named* queue for a speaker nobody will ever name, and it
    is per meeting because the numbering is.
    """
    channels = (meeting.transcription.get("channels") or {}).values()
    labels = {
        label
        for channel in channels
        if isinstance(channel, dict)
        for label, cluster in (channel.get("speakers") or {}).items()
        if not (isinstance(cluster, dict) and (cluster.get("echo") or cluster.get("noise")))
    }
    return sorted(labels - set(meeting.speaker_names))


def namesake_in(meeting: Meeting, db: VoicesDB, person: Person, ignore: str = "") -> Person | None:
    """Somebody *else* in this meeting whom the transcript would call by the same short name.

    A transcript label is the short name and nothing more, so two people who
    share one cannot be told apart inside a single file — and `forget` and
    `rename` rewrite labels by that name across the meetings `speaker_names`
    says a person is in. Two Annas in different meetings are fine; two Annas in
    **one** meeting would make every later label edit hit both. So
    :func:`referat.label.apply_name` refuses the second Anna into a meeting that
    already has one, and :func:`referat.label.rename_person` refuses a short
    name that would create the same situation retroactively.

    `ignore` is the label being named, so a speaker being *re*-filed does not
    collide with themself.
    """
    for label, value in meeting.speaker_names.items():
        if label == ignore:
            continue
        other = db.get(person_id(value))
        if (
            other is not None
            and other.id != person.id
            and other.short.casefold() == person.short.casefold()
        ):
            return other
    return None


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


def is_noise(meeting: Meeting, speaker: str) -> bool:
    """Whether `meta.json` records this cluster as noise rather than a person.

    :func:`is_echo`'s counterpart for the other verdict, read off the stored
    record for the same reason: it was a person's decision, and re-deriving it
    is not possible even in principle.
    """
    cluster = _stored_cluster(meeting, speaker)
    return bool(cluster and cluster.get("noise"))


def mark_stored_noise(meeting: Meeting, speaker: str) -> bool:
    """Flag one cluster as noise in the channel's `speakers` block. Does not save.

    False when there is no such cluster. Clears the stored snippet list the way
    :func:`referat.bleed.mark_clusters` does, because the caller deletes the
    files and a record of paths that no longer resolve is a small lie every later
    reader has to work around once. The embedding stays — inert, since both
    readers of it refuse a noise cluster — and no name is touched, because
    :func:`referat.noise.complaint` refuses a named cluster before this is reached.
    """
    entry = _stored_cluster(meeting, speaker)
    if entry is None:
        return False
    entry["noise"] = True
    entry["snippets"] = []
    return True


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
    person, why = db.resolve(owner)
    if why:
        # Two people the owner's name fits equally. Not a guess to make on a
        # transcription thread: the owner is the one person every meeting is
        # scoped to, and the config is where this gets fixed.
        log.error("%s: not adding an owner voiceprint -- %s", meeting.id, why)
        return False
    if cluster.name and (person is None or cluster.name != person.id):
        # Already recognised as somebody else. Believe the match over the config:
        # filing their voice under the owner's name is the one outcome worth
        # refusing outright.
        log.warning(
            "%s: the microphone was identified as %s, not %s; not adding an owner voiceprint",
            meeting.id,
            db.display(cluster.name),
            owner,
        )
        return False
    if person is None:
        # The first meeting on a fresh database: the owner's record is created
        # from the config's spelling, full and short alike, and grows a full
        # name the day `referat person rename` gives it one.
        person = db.new_person(owner)
    db.add(person.id, cluster.embedding, meeting.id, cluster.label)
    db.save()
    log.info(
        "added an owner voiceprint for %s (%s) from %s of %s",
        person.name,
        person.id,
        cluster.label,
        meeting.id,
    )
    return True


def owner_person(config: Config) -> Person | None:
    """The owner's record, or None when nobody on file answers to `[speakers].owner_name`.

    A fresh load each call, for the reason :func:`referat.hotwords.collect`
    reads the database live: a `--forget` or a rename must be seen by the next
    caller, and there is no cache to go stale.
    """
    return VoicesDB.load(config).owner(config)
