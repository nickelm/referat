r"""`referat label` — putting names on the speakers identification could not.

Step 7b matches a meeting's speaker clusters against the known-voices database on
the way through the pipeline. The ones it could not place stay `SPEAKER_NN`, and
this is where they get a name: play the snippets that were cut for them, ask who
that was, and file the embedding under the answer. That single act does two
things at once — it fixes this transcript, and it teaches the database a voice,
so the next meeting recognises the person without being asked.

**Cheap on purpose.** The embedding was computed during the pipeline and written
into `meta.json`, so naming somebody costs a JSON read, a WAV playback and a
vector append. Nothing here imports torch, and nothing here needs the
`transcribe` extra: nobody should have to keep three gigabytes of model resident
in order to type "Anna".

**The speech is never touched.** A speaker label is metadata that happens to live
in `transcript.md`, and rewriting it is the only edit anything in Referat is
permitted to make to an existing transcript. :func:`relabel_transcript` matches
the label field alone, anchored on the timestamp that precedes it.

**Order of writes matters.** The database first, then `meta.json`, then the
transcript, then the snippets. A crash anywhere in that sequence leaves the voice
*known*, which is the recoverable state: a later `referat rerun` re-derives the
name from the database by itself. The reverse order would lose the voiceprint and
keep the label, which nothing can undo.

The interactive prompt and the primitives underneath it are kept apart, because
step 11's labeling webview and step 20's dialog drive :func:`apply_name` and
:func:`forget` rather than reimplementing the matching in TypeScript or in Qt.

**There are two layers below the prompt and they are not the same layer.**
:func:`apply_name` is the primitive and knows the order of writes; it is called
by the pipeline as well. :func:`name_speaker` is the *operation* — the primitive
plus the reserved-name rule, the meeting lookup, the refusal to rename somebody
who already has a name, and the dashboard regeneration — and it is what every
surface calls. The distinction is `cli.apply_tags`' against `projects.add_tags`,
and it exists for the same reason: a surface reaching past the guards would be
the second implementation of them.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

from referat import index, paths, people, voices
from referat.config import Config
from referat.meeting import Meeting, load_meetings, resolve_meeting

log = logging.getLogger(__name__)

TIMESTAMP = r"\[\d{2}:\d{2}:\d{2}\]"
"""The `[HH:MM:SS]` :func:`referat.transcribe.render_transcript` writes."""

FUZZY_CUTOFF = 0.7
"""How close a typo has to be to an existing name before it is worth asking about."""

SNIPPET_RATE = 16000
"""What :func:`referat.voices.write_snippet` writes, so nothing here resamples."""

SNIPPET_GAP_SECONDS = 0.25
"""Silence between snippets played back to back, so two clips do not run together."""

HELP = """  <name>  name this speaker        r  replay the snippets
  <n>     pick a name from above    s  skip, and ask again next time
                                    q  stop here"""

HELP_NO_AUDIO = """  <name>  name this speaker        s  skip, and ask again next time
  <n>     pick a name from above    q  stop here"""


# --- Rewriting the labels ---------------------------------------------------


def relabel_transcript(path: Path, mapping: dict[str, str]) -> int:
    """Rewrite speaker labels in a transcript, returning how many lines changed.

    **Only the label field.** The pattern is anchored on the timestamp and the
    colon that delimit it, and the replacement never touches the text after that
    colon, so no amount of a speaker's name appearing in what they said can
    change a word of it. Written through :func:`referat.paths.write_text_atomic`,
    so an interrupted rename leaves the previous transcript intact rather than
    half of a new one.
    """
    usable = {old: new for old, new in mapping.items() if old and new and old != new}
    if not usable:
        return 0
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        log.warning("cannot read %s", path, exc_info=True)
        return 0

    changed = 0
    lines = text.split("\n")
    pattern = re.compile(rf"^({TIMESTAMP} )({'|'.join(re.escape(k) for k in usable)})(: )")
    for index, line in enumerate(lines):
        replaced, count = pattern.subn(lambda m: m[1] + usable[m[2]] + m[3], line, count=1)
        if count:
            lines[index] = replaced
            changed += 1
    if changed:
        paths.write_text_atomic(path, "\n".join(lines))
    return changed


# --- Naming and forgetting --------------------------------------------------


def apply_name(config: Config, meeting: Meeting, speaker: str, name: str) -> bool:
    """File `speaker`'s voice under `name` everywhere it belongs.

    The database, then `meta.json`, then the transcript, then the snippets — see
    the module docstring for why that order and no other. Returns False when
    there is no embedding to file, which is the one case that cannot be repaired
    by asking again.

    **An echo cluster is refused outright.** `voices.unknown_speakers` already
    stops one being offered, but that is the wrong place to rely on: this function
    is reached directly by `referat label <id> --speaker SPEAKER_NN --name <n>`,
    which is the path the VS Code extension uses and which asks nothing about who
    is unknown. Naming an echo cluster files a voiceprint recorded off a
    loudspeaker under a real person's name, and that print then sits in the
    database producing false accepts that `match_margin` cannot catch, because the
    margin compares names and this one is filed under the right one.
    """
    if voices.is_echo(meeting, speaker):
        log.warning(
            "%s: %s is the loopback coming back into the microphone, not a person to name",
            meeting.id,
            speaker,
        )
        return False
    embedding = voices.stored_embedding(meeting, speaker)
    if embedding is None:
        log.warning("%s has no stored embedding in %s", speaker, meeting.id)
        return False

    db = voices.VoicesDB.load(config)
    db.add(name, embedding, meeting.id, speaker)
    db.save()

    meeting.speaker_names[speaker] = name
    voices.set_stored_name(meeting, speaker, name)
    meeting.save()

    changed = relabel_transcript(meeting.transcript_path, {speaker: name})
    if not changed:
        # `meta.json` now names somebody the Markdown does not, which is the one
        # way these two can drift apart while every write reported success:
        # `relabel_transcript` returns 0 both for "no such label in the file" and
        # for a transcript it could not read. Neither is fatal — the name is
        # filed, and `referat relabel` can put it in the file later — but a
        # silent 0 here is how a reader ends up trusting the wrong one.
        log.warning(
            "%s: %s is %s in meta.json, but no line of %s carried that label",
            meeting.id,
            speaker,
            name,
            meeting.transcript_path.name,
        )
    removed = voices.drop_snippets(meeting, speaker)
    log.info(
        "%s: %s is %s (%d line(s) relabeled, %d snippet(s) deleted)",
        meeting.id,
        speaker,
        name,
        changed,
        removed,
    )
    return True


def forget(config: Config, name: str) -> tuple[int, int]:
    """Delete a person from the database and revert their labels everywhere.

    A real deletion, not a tombstone: the embeddings go, every transcript that
    called somebody by this name goes back to calling them by the number they had
    before, and `meta.json` loses the name from `speaker_names` and from the
    per-channel record alike. Nothing identifying the person is left anywhere.
    Returns `(embeddings deleted, meetings reverted)`.

    This is what `speaker_names` is kept per meeting for. The Markdown alone says
    `Anna` and cannot say which `SPEAKER_NN` she used to be, and the numbers are
    per meeting — so without that mapping there would be no way back.

    **One name can be several labels in one meeting**, and the revert has to be
    deliberate about it. Diarization splits a person across two clusters often
    enough that `TODO.md` has an item about it, and the person naming them puts
    the same name on both on purpose — `2026-09-01_2102` has the owner as
    `SPEAKER_01` and `SPEAKER_02`. The transcript then says `Anna` on lines that
    came from either, and nothing in it records which, so every one of them has to
    revert to a single number. The **lowest** label wins, which is at least stable
    across runs; the alternative was whichever one `speaker_names` happened to
    iterate last, which is what this used to do.

    That merge is honest rather than merely convenient: the two labels share a
    name because a person said they are the same voice, so collapsing them asserts
    nothing nobody asserted already. What it costs is the other label, which keeps
    its embedding in `meta.json` but now has no lines and no snippets, so
    `referat label` can offer it nothing to recognise. Deleting a person is
    already one-way — see the `--forget` item under "Surfaced later".
    """
    db = voices.VoicesDB.load(config)
    deleted = db.forget(name)
    db.save()

    reverted = 0
    for meeting in load_meetings(config):
        back = {label: n for label, n in meeting.speaker_names.items() if n == name}
        if not back:
            continue
        if len(back) > 1:
            log.info(
                "%s: %s was %s; all of them revert to %s, which is where those "
                "lines are indistinguishable",
                meeting.id,
                name,
                ", ".join(sorted(back)),
                min(back),
            )
        relabel_transcript(meeting.transcript_path, {name: min(back)})
        for label in back:
            meeting.speaker_names.pop(label, None)
            # The per-channel record names them too, in `name` and inside the
            # `match` block. A deletion that left those behind would not be one.
            voices.clear_stored_name(meeting, label)
        meeting.save()
        reverted += 1
        log.info("%s: %s is %s again", meeting.id, name, ", ".join(sorted(back)))
    return deleted, reverted


# --- Playback ---------------------------------------------------------------


def sample_lines(meeting: Meeting, speaker: str, count: int = 3) -> list[str]:
    """A few of the things this speaker said, for when there is no audio left.

    That happens after a `--forget`: the snippets were deleted the moment the
    person was first named, and reverting the label cannot bring them back. Three
    of their lines is thin evidence, but it beats being unable to name them at
    all — and the embedding `meta.json` kept is still perfectly good to file.
    """
    try:
        text = meeting.transcript_path.read_text(encoding="utf-8")
    except OSError:
        return []
    pattern = re.compile(rf"^{TIMESTAMP} {re.escape(speaker)}: (.+)$")
    return [m[1] for line in text.split("\n") if (m := pattern.match(line))][:count]


def concatenate(clips: list[Path]) -> tuple[Any, str]:
    """A speaker's snippets decoded into one array, or the complaint that stopped it.

    The shape :func:`referat.meeting.resolve_meeting` uses — a value and an
    unprefixed sentence — because the two callers say it in different places: the
    prompt prints it, and the command center's dialog puts it beside a disabled
    button.

    **A clip that will not decode is skipped rather than fatal.** Playback is
    worth exactly one thing, a person recognising a voice, and one unreadable
    snippet out of three costs a third of the evidence rather than all of it. The
    complaint is for having nothing left to play at all.

    Snippets are written at 16 kHz, so this never reaches `decode_wav`'s
    resampler — which is the reason naming somebody needs neither scipy nor any
    part of the `transcribe` extra beyond a WAV reader.
    """
    try:
        import numpy as np

        from referat.transcribe import decode_wav
    except Exception:
        log.warning("cannot decode snippets", exc_info=True)
        return None, "cannot read the snippets"

    parts = []
    gap = np.zeros(int(SNIPPET_RATE * SNIPPET_GAP_SECONDS), dtype=np.float32)
    for clip in clips:
        try:
            parts.append(decode_wav(clip))
        except Exception:
            log.warning("could not decode %s", clip, exc_info=True)
    if not parts:
        return None, "none of the snippets could be read"
    # A gap between them, or two clips of the same voice run together and sound
    # like one utterance with a join in it.
    joined = [part for clip in parts for part in (clip, gap)][:-1]
    return np.concatenate(joined), ""


def play(clips: list[Path]) -> None:
    """Play a speaker's snippets back to back, blocking. Never raises.

    The prompt's half of playback. :func:`play_async` is the window's, and both
    go through :func:`concatenate` so that a snippet the terminal can play is one
    the command center can play too.
    """
    audio, complaint = concatenate(clips)
    if complaint:
        print(f"  ({complaint})")
        return
    try:
        import sounddevice as sd

        print(f"  playing {len(clips)} snippet(s), {audio.size / SNIPPET_RATE:.1f}s")
        sd.play(audio, SNIPPET_RATE)
        sd.wait()
    except Exception as exc:
        print(f"  could not play the snippets: {exc}")


def play_async(clips: list[Path]) -> str:
    """Start a speaker's snippets playing and return at once. The complaint, or `""`.

    `sounddevice.play` is asynchronous by itself — PortAudio pulls the array on
    its own callback thread — so a window needs no thread of its own here, and
    starting one would only give it something to join on close.

    **This is the tray's process, which is the process that records**, and that
    is the one thing to be careful about: playing a snippet through the speakers
    while a meeting is being recorded puts that person's earlier speech into
    `system.wav` through WASAPI loopback, where it is transcribed and diarized as
    if the far end had said it. Nothing in here can tell — the caller knows the
    recorder's state and :class:`referat.ui.speakers.SpeakerDialog` refuses the
    button for it. A transcript is evidence of what was said, and this is the one
    way a UI could quietly write something into one.
    """
    audio, complaint = concatenate(clips)
    if complaint:
        return complaint
    try:
        import sounddevice as sd

        sd.play(audio, SNIPPET_RATE)
    except Exception as exc:
        log.warning("could not play snippets", exc_info=True)
        return f"could not play the snippets: {exc}"
    return ""


def stop_playback() -> None:
    """Stop whatever :func:`play_async` started. Safe when nothing is playing."""
    try:
        import sounddevice as sd

        sd.stop()
    except Exception:
        log.debug("nothing to stop", exc_info=True)


# --- The prompt -------------------------------------------------------------


def _resolve(entry: str, known: list[str]) -> str | None:
    """Turn what was typed into a name, asking about near-misses. None to give up.

    A number picks off the list. A name close to one already in the database is
    **confirmed rather than applied**, so a typo creates a second person only when
    the user insists on it — the database has no way to merge two people back
    together afterwards.
    """
    if entry.isdigit():
        index = int(entry) - 1
        if 0 <= index < len(known):
            return known[index]
        print(f"  no name number {entry}")
        return None

    complaint = voices.name_complaint(entry)
    if complaint:
        print(f"  a name {complaint}")
        return None

    name = entry.strip()
    if name in known:
        return name
    close = difflib.get_close_matches(name, known, n=1, cutoff=FUZZY_CUTOFF)
    if close and _confirm(f"  Did you mean {close[0]}?"):
        return close[0]
    return name


def _confirm(question: str) -> bool:
    """A `[Y/n]` prompt defaulting to yes. False on end of input."""
    try:
        return input(f"{question} [Y/n] ").strip().lower() not in ("n", "no")
    except EOFError:
        return False


def _label_speaker(config: Config, meeting: Meeting, speaker: str) -> str:
    """Ask who one speaker is. Returns `named`, `skipped` or `quit`."""
    clips = voices.snippet_paths(meeting, speaker)
    # Plain ASCII on purpose: this prints to a Windows console whose code page
    # is not UTF-8, and a dash is not worth a UnicodeEncodeError.
    #
    # The channel is said here for the same reason `label --json` carries it, and
    # said in both places so the terminal and the sidebar cannot describe the same
    # speaker differently: which file a voice arrived in is the strongest hint
    # available about who they are, and it was not being shown at all.
    channel = voices.speaker_channel(meeting, speaker)
    where = {"mic": "  (your microphone)", "system": "  (the call)"}.get(channel, "")
    print(f"\n{meeting.id}  {speaker}{where}")
    if clips:
        play(clips)
    else:
        # Most likely this speaker was named once and then forgotten: the
        # snippets went when the name arrived, and reverting the label cannot
        # bring them back. The embedding is still in `meta.json`, though, so they
        # can be named — by reading rather than by listening. Skipping instead
        # would make the speaker unnameable forever, which is the worse end.
        lines = sample_lines(meeting, speaker)
        if not lines:
            print("  no audio and no lines left for this speaker; skipping")
            return "skipped"
        print("  no audio left for this speaker. What they said:")
        for line in lines:
            print(f"    {line[:100]}")

    while True:
        known = voices.VoicesDB.load(config).names()
        if known:
            print("  known: " + ", ".join(f"{i}) {n}" for i, n in enumerate(known, start=1)))
        print(HELP if clips else HELP_NO_AUDIO)
        try:
            entry = input("  who was that? ").strip()
        except EOFError:
            return "quit"
        if not entry:
            continue
        if entry.lower() == "q":
            return "quit"
        if entry.lower() == "s":
            # Not a decision. The speaker stays unknown and comes back next time.
            return "skipped"
        if entry.lower() == "r" and clips:
            play(clips)
            continue

        name = _resolve(entry, known)
        if name is None:
            continue
        if apply_name(config, meeting, speaker, name):
            print(f"  {speaker} is {name}")
            return "named"
        print("  no embedding was stored for this speaker; nothing to file")
        return "skipped"


# --- Entry points -----------------------------------------------------------


def label_meeting(config: Config, meeting: Meeting) -> str:
    """Walk one meeting's unknown speakers. Returns `done` or `quit`."""
    for speaker in voices.unknown_speakers(meeting):
        if _label_speaker(config, meeting, speaker) == "quit":
            return "quit"
    return "done"


def run(config: Config, meeting_id: str | None) -> int:
    """`referat label [<meeting-id>]`. With no id, every meeting that still needs it.

    Oldest first, so the backlog is worked through in the order it accumulated
    and the database learns each voice at the earliest meeting it appears in.
    """
    if meeting_id:
        meeting = _resolve_meeting(config, meeting_id)
        if meeting is None:
            return 1
        candidates = [meeting]
    else:
        candidates = load_meetings(config)

    pending = [m for m in candidates if voices.unknown_speakers(m)]
    if not pending:
        print("Nothing to label." if not meeting_id else f"{meeting_id}: nothing to label.")
        return 0

    total = sum(len(voices.unknown_speakers(m)) for m in pending)
    print(f"{total} unknown speaker(s) across {len(pending)} meeting(s).")
    for meeting in pending:
        if label_meeting(config, meeting) == "quit":
            print("\nStopped. The speakers left unnamed will be asked about again.")
            break
    # Naming somebody is the only thing outside the pipeline that changes the
    # dashboard's Unnamed column, so it regenerates here too rather than going
    # stale until the next meeting. Never raises.
    index.write_index(config)
    return 0


def _resolve_meeting(config: Config, meeting_id: str) -> Meeting | None:
    """One meeting by id, complaining to stderr the way :func:`run` does.

    The lookup itself is :func:`referat.meeting.resolve_meeting`, shared with the
    CLI's own commands that take an id — only the `referat label:` prefix is ours.
    """
    meeting, why = resolve_meeting(config, meeting_id)
    if meeting is None:
        print(f"referat label: {why}", file=sys.stderr)
    return meeting


def label_document(config: Config, meeting: Meeting) -> dict[str, Any]:
    """One meeting's unnamed speakers, and everything a labeling surface needs.

    What `referat label <id> --json` prints and what
    :class:`referat.ui.speakers.SpeakerDialog` renders — the same builder, for
    the same reason `list_document` and `transcript_document` are: the extension
    shells out because it is TypeScript, the window imports because it is already
    a Python process in this package, and neither may hold an opinion about a
    meeting the other does not share.

    The snippet *paths* rather than the audio: they are ordinary WAVs on disk,
    which a webview loads through `asWebviewUri` and a window decodes in place.
    `has_embedding` is the one case naming cannot repair — a speaker whose
    embedding never made it into `meta.json` has nothing to file, so a surface
    must say so rather than offer a field that is guaranteed to be refused.

    `channel` and `owner` answer the question a panel could not: *is this me?*
    Naming four speakers after one Teams call, one of them was the person doing
    the naming, and nothing on screen said that cluster had come out of their own
    microphone. `owner` crosses as a string rather than as a pre-sorted
    `known_names` because what the owner is *called* is Python's to know and the
    order chips appear in is the surface's to decide. It is `""` when
    `[speakers].owner_name` is unset, which is rendered as no chip rather than an
    empty one.

    `gallery` is phase 3's addition and the project-scoped half of identification:
    `scoped` is the people this meeting's tags associate with and `rest` is
    everybody else, the two together being exactly `known_names`. `tags` is what
    did the scoping, carried beside them so a surface can say *this meeting has no
    project, so every name is offered* as a **fact it was told** rather than as
    something inferred from `scoped` being short — which would be the same shape
    of mistake as inferring a meeting's lifecycle from which files exist. It is
    also what makes the nudge honest: tag first, then label. The join is
    :func:`referat.people.gallery`'s and is computed here rather than in a
    surface, because working out which people belong to a project in TypeScript —
    or in a dialog — would be a second implementation of the one thing this whole
    arrangement exists to have one of. It **narrows and orders what a human is
    offered and nothing else**: the automatic match during the pipeline stays
    global, and every name written is still somebody's decision.
    """
    scoped, rest = people.gallery(config, meeting)
    return {
        "meeting": meeting.id,
        "dir": str(meeting.dir),
        "known_names": voices.VoicesDB.load(config).names(),
        "owner": config.speakers.owner_name.strip(),
        "gallery": {"tags": list(meeting.tags), "scoped": scoped, "rest": rest},
        "speakers": [
            {
                "speaker": speaker,
                "channel": voices.speaker_channel(meeting, speaker),
                "snippets": [str(p) for p in voices.snippet_paths(meeting, speaker)],
                "lines": sample_lines(meeting, speaker),
                "has_embedding": voices.stored_embedding(meeting, speaker) is not None,
            }
            for speaker in voices.unknown_speakers(meeting)
        ],
    }


def run_json(config: Config, meeting_id: str) -> int:
    """`referat label <id> --json` — the document above, printed."""
    meeting = _resolve_meeting(config, meeting_id)
    if meeting is None:
        return 1
    print(json.dumps(label_document(config, meeting), indent=2))
    return 0


def name_speaker(config: Config, meeting_id: str, speaker: str, name: str) -> tuple[bool, str]:
    """Name one speaker, with every rule that governs it. The only implementation.

    `referat label <id> --speaker <s> --name <n>` is this printed, and the
    command center's dialog is this in process — which is the point, because the
    rules are not in :func:`apply_name`. That function is the primitive: it files
    the embedding, writes `meta.json`, rewrites the labels and drops the snippets,
    in that order and no other. What makes *naming* correct is the four things
    around it — the reserved-name rule, resolving the meeting, refusing a speaker
    who is not waiting for a name, and regenerating the dashboard afterwards —
    and a surface growing its own copy of those is exactly the second
    implementation the one-implementation rule exists to prevent. `cli.apply_tags`
    is the same shape for the same reason.

    Returns the value-and-unprefixed-complaint pair
    :func:`referat.meeting.resolve_meeting` returns, because the prefix is the
    caller's: the CLI says `referat label:` and a dialog says nothing at all.

    **Renaming is a different operation from naming**, so a speaker who already
    has one is refused rather than overwritten — the way back is `--forget`,
    which also reverts the labels that name wrote everywhere else.

    :func:`apply_name` refuses an echo cluster on its own and says so in the log
    alone, so the `False` it returns is reported here as the one thing a caller
    can act on: there is nothing to file.
    """
    complaint = voices.name_complaint(name)
    if complaint:
        return False, f"a name {complaint}"

    meeting, why = resolve_meeting(config, meeting_id)
    if meeting is None:
        return False, why

    if speaker not in voices.unknown_speakers(meeting):
        if name_it_has := meeting.speaker_names.get(speaker):
            why = f"is already {name_it_has}"
        elif voices.is_echo(meeting, speaker):
            # Said in full rather than as "not an unnamed speaker", which is what
            # `unknown_speakers` filtering it out would otherwise reduce it to.
            # This is reached by `--speaker`, which is typed by somebody who can
            # see the cluster in `referat show` and is owed the actual reason —
            # and the reason is the one that costs a database entry if ignored.
            why = (
                "is the loopback coming back into the microphone rather than a "
                "person, so a name here would file a voiceprint of a loudspeaker"
            )
        else:
            why = f"is not an unnamed speaker in {meeting.id}"
        return False, f"{speaker} {why}"

    if not apply_name(config, meeting, speaker, name):
        return False, f"no embedding was stored for {speaker}; nothing to file"

    # `apply_name` deliberately does not touch the dashboard — it is a primitive,
    # and the pipeline calls it too. Every *entry point* that names somebody has
    # to, or the Unnamed column goes stale the moment anything but the prompt is
    # used.
    index.write_index(config)
    return True, f"{speaker} is {name}"


def run_apply(config: Config, meeting_id: str, speaker: str, name: str) -> int:
    """`referat label <id> --speaker <s> --name <n>` — the prompt's answer, given."""
    named, message = name_speaker(config, meeting_id, speaker, name)
    if not named:
        print(f"referat label: {message}", file=sys.stderr)
        return 1
    print(message)
    return 0


def forget_person(config: Config, name: str) -> tuple[bool, str]:
    """Delete a person, with every rule that governs it. The only implementation.

    `referat label --forget <name>` is this with a confirmation in front of it, and
    the command center's people page is this in process — which is the point, for
    the reason :func:`name_speaker` sits above :func:`apply_name`. That function is
    the primitive: it empties the database entry and reverts the labels, in that
    order and no other. What makes *forgetting* correct is the two things around
    it — refusing a name nothing is filed under, and regenerating the dashboard
    afterwards, since reverting a label puts the Unnamed column back up — and a
    surface growing its own copy of those is exactly the second implementation the
    one-implementation rule exists to prevent.

    Returns the value-and-unprefixed-complaint pair
    :func:`referat.meeting.resolve_meeting` returns, because the prefix is the
    caller's: the CLI says `referat label:` and a dialog says nothing at all.

    **The confirmation is deliberately not here.** It is not a rule about the
    operation, it is a question asked of a terminal — `_confirm` reads `input()`
    and answers *no* on EOF, which is the whole reason `--yes` exists — and a
    window asks it with a modal instead. Both are asking the same thing and
    neither is asking it twice.
    """
    db = voices.VoicesDB.load(config)
    if name not in db.people:
        return False, f"{name} is not in the known-voices database"

    deleted, reverted = forget(config, name)
    # Forgetting puts labels back to SPEAKER_NN, so the Unnamed column goes up.
    index.write_index(config)
    return True, f"Deleted {deleted} voiceprint(s) of {name}; reverted {reverted} transcript(s)."


def run_forget(config: Config, name: str, assume_yes: bool = False) -> int:
    """`referat label --forget <name>` — the confirmation, and one line of dispatch."""
    if name not in voices.VoicesDB.load(config).people:
        # Asked before the confirmation rather than after it, so a mistyped name
        # is a refusal rather than a question about deleting somebody who does not
        # exist. `forget_person` checks it again because it is its rule, not this
        # command's, and a caller with no terminal reaches it directly.
        print(f"referat label: {name} is not in the known-voices database", file=sys.stderr)
        return 1
    if not assume_yes and not _confirm(
        f"Delete {name} and revert their labels in every transcript?"
    ):
        print("Nothing was deleted.")
        return 0

    forgotten, message = forget_person(config, name)
    if not forgotten:
        print(f"referat label: {message}", file=sys.stderr)
        return 1
    print(message)
    return 0


def run_drop_voiceprint(
    config: Config, meeting_id: str, speaker: str, assume_yes: bool = False
) -> int:
    """`referat label <meeting-id> --drop-voiceprint SPEAKER_NN`.

    The narrow counterpart to `--forget`, for a cluster that was named correctly
    and recorded wrongly: the remote person's voice coming back off a loudspeaker
    into the room microphone, which somebody was offered by `referat label` and
    quite reasonably called by their name. The name is right. The recording is a
    loudspeaker, and the print it produced makes `voices.match` accept a
    loudspeaker as that person — a false accept `match_margin` cannot catch,
    because the margin compares *names* and this one is filed under the right one.

    So this touches the database and nothing else. It reverts no label, because
    the labels are correct; that is the whole difference from `forget`, and it is
    why this could not have been a flag on it.
    """
    meeting, complaint = resolve_meeting(config, meeting_id)
    if meeting is None:
        print(f"referat label: {complaint}", file=sys.stderr)
        return 1

    db = voices.VoicesDB.load(config)
    doomed = {
        name: sum(1 for p in prints if p.meeting == meeting.id and p.speaker == speaker)
        for name, prints in db.people.items()
    }
    doomed = {name: n for name, n in doomed.items() if n}
    if not doomed:
        print(
            f"referat label: no voiceprint in the database came from {speaker} "
            f"of {meeting.id}"
        )
        return 0

    who = ", ".join(f"{name} ({n})" for name, n in sorted(doomed.items()))
    if not assume_yes and not _confirm(
        f"Delete the voiceprint(s) {speaker} of {meeting.id} contributed to {who}? "
        "Their transcript labels are not touched"
    ):
        print("Nothing was deleted.")
        return 0

    try:
        removed = db.drop(meeting.id, speaker)
    except ValueError as exc:
        print(f"referat label: {exc}", file=sys.stderr)
        return 1
    db.save()
    total = sum(removed.values())
    left = {name: len(db.people.get(name, [])) for name in removed}
    print(
        f"Deleted {total} voiceprint(s) from {speaker} of {meeting.id}: "
        + ", ".join(f"{name} keeps {n}" for name, n in sorted(left.items()))
    )
    # No `index.write_index`: no label moved, so no column on the dashboard did.
    return 0
