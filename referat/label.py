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
step 11's labeling webview is meant to drive :func:`apply_name` and
:func:`forget` rather than reimplement the matching in TypeScript.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import sys
from pathlib import Path

from referat import index, paths, voices
from referat.config import Config
from referat.meeting import Meeting, load_meetings

log = logging.getLogger(__name__)

TIMESTAMP = r"\[\d{2}:\d{2}:\d{2}\]"
"""The `[HH:MM:SS]` :func:`referat.transcribe.render_transcript` writes."""

FUZZY_CUTOFF = 0.7
"""How close a typo has to be to an existing name before it is worth asking about."""

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
    """
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
    """
    db = voices.VoicesDB.load(config)
    deleted = db.forget(name)
    db.save()

    reverted = 0
    for meeting in load_meetings(config):
        back = {label: n for label, n in meeting.speaker_names.items() if n == name}
        if not back:
            continue
        relabel_transcript(meeting.transcript_path, {n: label for label, n in back.items()})
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


def play(clips: list[Path]) -> None:
    """Play a speaker's snippets back to back. Never raises — it only costs a replay."""
    try:
        import sounddevice as sd

        from referat.transcribe import decode_wav
    except Exception:
        print("  (cannot play audio: no working sound device)")
        return

    for index, clip in enumerate(clips, start=1):
        try:
            # Snippets are written at 16 kHz, so this never reaches decode_wav's
            # resampler and therefore never needs scipy.
            audio = decode_wav(clip)
            print(f"  [{index}/{len(clips)}] {clip.name} ({audio.size / 16000:.1f}s)")
            sd.play(audio, 16000)
            sd.wait()
        except Exception as exc:
            print(f"  could not play {clip.name}: {exc}")


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
    print(f"\n{meeting.id}  {speaker}")
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
    """One meeting by id, complaining to stderr the way :func:`run` does."""
    folder = paths.find_meeting_dir(config.meeting_roots(), meeting_id)
    if folder is None:
        print(f"referat label: no meeting {meeting_id}", file=sys.stderr)
        return None
    meeting = Meeting.load(folder)
    if meeting is None:
        print(f"referat label: cannot read {folder / paths.META_JSON}", file=sys.stderr)
    return meeting


def run_json(config: Config, meeting_id: str) -> int:
    """`referat label <id> --json` — everything the labeling webview needs.

    The extension gets the snippet *paths* rather than the audio: they are
    ordinary WAVs on disk and a webview can load them through
    `asWebviewUri`. `has_embedding` is the one case naming cannot repair — a
    speaker whose embedding never made it into `meta.json` has nothing to file,
    so the webview must show that rather than offer a field that will fail.
    """
    meeting = _resolve_meeting(config, meeting_id)
    if meeting is None:
        return 1

    document = {
        "meeting": meeting.id,
        "dir": str(meeting.dir),
        "known_names": voices.VoicesDB.load(config).names(),
        "speakers": [
            {
                "speaker": speaker,
                "snippets": [str(p) for p in voices.snippet_paths(meeting, speaker)],
                "lines": sample_lines(meeting, speaker),
                "has_embedding": voices.stored_embedding(meeting, speaker) is not None,
            }
            for speaker in voices.unknown_speakers(meeting)
        ],
    }
    print(json.dumps(document, indent=2))
    return 0


def run_apply(config: Config, meeting_id: str, speaker: str, name: str) -> int:
    """`referat label <id> --speaker <s> --name <n>` — the prompt's answer, given.

    Every rule the prompt enforces is enforced here too, and in Python: the
    reserved-name check is :func:`referat.voices.name_complaint`, and it stays
    the only copy of that rule rather than being restated in the webview that
    calls this.
    """
    complaint = voices.name_complaint(name)
    if complaint:
        print(f"referat label: a name {complaint}", file=sys.stderr)
        return 1

    meeting = _resolve_meeting(config, meeting_id)
    if meeting is None:
        return 1

    if speaker not in voices.unknown_speakers(meeting):
        known = meeting.speaker_names.get(speaker)
        why = f"is already {known}" if known else f"is not an unnamed speaker in {meeting.id}"
        print(f"referat label: {speaker} {why}", file=sys.stderr)
        return 1

    if not apply_name(config, meeting, speaker, name):
        print(
            f"referat label: no embedding was stored for {speaker}; nothing to file",
            file=sys.stderr,
        )
        return 1

    # `apply_name` deliberately does not touch the dashboard — it is a primitive,
    # and the pipeline calls it too. Every *entry point* that names somebody has
    # to, or the Unnamed column goes stale the moment the webview is used.
    index.write_index(config)
    print(f"{speaker} is {name}")
    return 0


def run_forget(config: Config, name: str, assume_yes: bool = False) -> int:
    """`referat label --forget <name>`."""
    db = voices.VoicesDB.load(config)
    if name not in db.people:
        print(f"referat label: {name} is not in the known-voices database", file=sys.stderr)
        return 1
    if not assume_yes and not _confirm(
        f"Delete {name} and revert their labels in every transcript?"
    ):
        print("Nothing was deleted.")
        return 0

    deleted, reverted = forget(config, name)
    # Forgetting puts labels back to SPEAKER_NN, so the Unnamed column goes up.
    index.write_index(config)
    print(f"Deleted {deleted} voiceprint(s) of {name}; reverted {reverted} transcript(s).")
    return 0
