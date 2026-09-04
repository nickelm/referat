r"""Who Referat knows, and which projects and meetings they appear in.

Phase 3 of build step 20 needs one question answered: *when this meeting carries
a project, which of the known voices belong to that thread of work?* The names
it offers a person naming a speaker are then that shorter list, with everybody
else behind a *show all*. Nothing here decides anything — it **narrows and orders
what a human is offered**, and every name written is still somebody's decision.

Phase 5 asks the same join from the other end — *who is this person, and where do
they appear?* — which is :func:`directory`. Both are one walk of the same two
files, and :func:`project_people` is expressed on top of it rather than beside
it: two scans that had to agree would be the first two things to disagree.

**The association is derived every time and stored nowhere.** There is no
membership list, no `people` key in `projects.json`, and there should not be:
that would be a fourth thing to keep in step with `meta.json`'s `tags`, the
voices database and the transcripts, and the first one to disagree with them.
Two files already record everything needed, in two directions:

- **Where a voiceprint was filed from.** :class:`referat.voices.Voiceprint`
  stamps every embedding with the meeting and the cluster it came out of, and
  that meeting carries `tags`.
- **Where a person appears.** A meeting's `speaker_names` names them, and that
  meeting carries `tags`.

**Neither direction subsumes the other**, which is why both are here. Somebody
recognised automatically by :func:`referat.voices.identify` files nothing new, so
the database records none of the meetings they were merely *heard* in — that is
the first direction missing them. And a `referat rerun` renumbers a meeting's
clusters and re-derives its names, so a person who is not re-matched drops out of
that meeting's `speaker_names` while the print filed from it keeps their
provenance — that is the second direction missing them.

**A person is names, counts and meeting ids here, and never an embedding.** This
module reads the voices database for its provenance and its names alone; nothing
it returns can be turned back into a voiceprint, nothing it returns is a path
into `.voices/`, and nothing it returns is written anywhere. The database is
biometric personal data about people who never asked to be in it, and a module
*about* the people in it is the easiest place to forget that — which is why
`referat people` carries the same warning in its own help text.

**Project membership never touches clustering.** Diarization and
:func:`referat.voices.match` run during the pipeline, before anything has been
tagged, and are untouched by everything here: narrowing a gallery changes what a
person is offered, while changing the clustering would change what the machine
decided. That line does not move.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from referat.config import Config
from referat.meeting import load_meetings
from referat.voices import VoicesDB

if TYPE_CHECKING:
    from referat.meeting import Meeting

log = logging.getLogger(__name__)


@dataclass
class Filing:
    """Where one voiceprint came from: a meeting, a cluster, and a date.

    :class:`referat.voices.Voiceprint`'s own provenance, carried across without
    the vector it was stamped onto. That stamp exists so a bad entry can be traced
    back and pulled out, and showing it is what makes a person page honest about
    what is actually stored under somebody's name.
    """

    meeting: str
    speaker: str
    added: str


@dataclass
class Person:
    """One name, and everywhere it appears. Never an embedding.

    `filed_from` and `appears_in` are the two directions of the join and are kept
    apart rather than unioned, because they answer different questions: the first
    is *what is stored under this name*, which is what `--forget` deletes, and the
    second is *where do they turn up*, which is most of them.

    `in_database` is False for a name a transcript still calls somebody by while
    the voices database holds nothing under it. That is drift — a `referat rerun`
    that renumbered past somebody, or a hand-edited `speaker_names` — and it is
    shown rather than filtered out, because it is otherwise invisible: nothing
    else on this machine compares those two files.
    """

    name: str
    prints: int = 0
    filed_from: list[Filing] = field(default_factory=list)
    appears_in: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    in_database: bool = True
    is_owner: bool = False
    in_untagged: bool = False
    """Whether any meeting they are seen in carries no tags at all.

    `tags` cannot say this: an untagged meeting contributes no id, so somebody
    seen in one archived project and one untagged meeting has exactly the same
    `tags` as somebody seen only in the archived one. They are not the same
    person to a reader, and :func:`referat.cli._inactive` is what needs to tell
    them apart — an untagged meeting is work nobody has *labelled* yet, which is
    an unknown, and an unknown must never be read as an ending.

    A meeting that has since been deleted does not count: it is absent from the
    tag map entirely, and reading that absence as "no tags" would make deleting a
    meeting quietly reactivate everybody who was in it.
    """


def directory(config: Config) -> list[Person]:
    """Everybody Referat knows a name for, sorted, with where each one appears.

    One walk of both meeting roots and one read of the voices database — the same
    two files :func:`project_people` joins, and the reason that function is
    expressed on this one rather than beside it.

    The result is **names, counts and ids**. No embedding and no path into
    `.voices/` crosses this boundary, which is the rule the module docstring
    states and the one a listing is easiest to break.

    A print filed from a meeting that has since been deleted keeps its filing —
    the stamp is what was recorded and saying otherwise would hide a voiceprint
    that really is on file — but contributes no tags, because there is no `tags`
    list left to read. `referat delete` removes a meeting and deliberately keeps
    the person.
    """
    meetings = load_meetings(config)
    tags_of = {meeting.id: list(meeting.tags) for meeting in meetings}
    db = VoicesDB.load(config)
    owner = config.speakers.owner_name.strip()

    found: dict[str, Person] = {}

    def entry(name: str) -> Person:
        return found.setdefault(name, Person(name=name, in_database=False))

    for name, prints in db.people.items():
        person = entry(name)
        person.in_database = True
        person.prints = len(prints)
        person.filed_from = [
            Filing(meeting=p.meeting, speaker=p.speaker, added=p.added) for p in prints
        ]

    for meeting in meetings:
        for name in sorted(set(meeting.speaker_names.values())):
            entry(name).appears_in.append(meeting.id)

    for person in found.values():
        person.is_owner = bool(owner) and person.name == owner
        seen = {f.meeting for f in person.filed_from} | set(person.appears_in)
        person.tags = sorted({tag for mid in seen for tag in tags_of.get(mid, ())})
        # `mid in tags_of` first, deliberately: a meeting that no longer exists is
        # missing from the map rather than empty in it, and treating the two the
        # same would make a deletion read as an untagged meeting.
        person.in_untagged = any(mid in tags_of and not tags_of[mid] for mid in seen)

    return [found[name] for name in sorted(found)]


def project_people(config: Config) -> dict[str, set[str]]:
    """Every project id mapped to the known names its meetings involve.

    Projects with nobody in them are simply absent rather than present and empty —
    a caller asks about the tags a meeting carries, and a missing key and an empty
    set say the same thing to `dict.get`.

    Both directions of the join are applied; see the module docstring for why
    neither is redundant. They are applied by :func:`directory`, which is where
    that walk now lives: this is its inverse, and computing it separately would be
    a second scan that has to agree with the first.
    """
    people: dict[str, set[str]] = {}
    for person in directory(config):
        for tag in person.tags:
            people.setdefault(tag, set()).add(person.name)
    return people


def gallery(config: Config, meeting: Meeting) -> tuple[list[str], list[str]]:
    """The names to offer for this meeting first, and the rest as a second list.

    `(scoped, rest)`, both sorted, and together **exactly** the known-voices
    database's own names — the caller may show one, the other or both, and cannot
    end up offering a name that is not in the database or losing one that is.

    An untagged meeting comes back `([], every name)`, which is the honest answer
    rather than a degenerate one: with no project there is nothing to scope on,
    and the surface shows the full gallery. That is the *nudge and never a gate*
    rule holding at the one place it costs something — a missing tag must never
    cost a name.

    **The owner is scoped into every meeting, whatever it is tagged with**, and
    that is not a special case bolted on: they pressed the button, so they were in
    the room, and a project they have not yet been named in is a project they have
    simply not been named in yet. Leaving them out put the one name a surface can
    honestly lead with — the microphone's own owner, which is what `channel` is
    carried for — behind a *show all*, on exactly the meetings where an unnamed
    mic cluster is most likely to be them.

    Scoping is intersected with the database rather than trusted from the join: a
    name in some meeting's `speaker_names` whom `referat label --forget` has since
    deleted is a name nothing can be filed under, and offering it would be
    offering a chip that is guaranteed to create a new person under an old
    spelling. The owner goes through the same intersection, so an
    `[speakers].owner_name` with no voiceprint behind it yet is not offered
    either.
    """
    known = VoicesDB.load(config).names()
    if not meeting.tags:
        return [], known
    associated = project_people(config)
    wanted = {name for tag in meeting.tags for name in associated.get(tag, ())}
    wanted.add(config.speakers.owner_name.strip())
    scoped = [name for name in known if name in wanted]
    return scoped, [name for name in known if name not in wanted]
