"""That a person is a record, and that renaming one reaches everything it should.

Build step 20c turned `voices.json`'s bare names into records keyed by an id,
made `speaker_names` store that id, and gave `referat person rename` the job of
propagating a changed short name into every transcript and every `[[Wikilink]]`
that carried the old one. This drives all of it against a synthetic database and
three synthetic meetings in the scratch directory: a version-1 file loading as
records, a legacy `speaker_names` resolving to the same id, a second person with
a colliding full name getting `-2`, the namesake refusal inside one meeting, the
rename with its relabel and its bounded note rewrite, a forget by id, and the
hotword list carrying both spellings and never the email.

**It never touches the real database** and never reads it. The embeddings here
are eight numbers each; nothing biometric is involved.

    .venv\\Scripts\\python.exe scripts\\person_fixture.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from referat import hotwords, label, noise, paths, people, voices  # noqa: E402
from referat.cli import people_document, transcript_document  # noqa: E402
from referat.config import load_config  # noqa: E402
from referat.meeting import Meeting, MeetingStatus, load_meetings  # noqa: E402

FAILURES: list[str] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if passed else 'MISS'} {name}{('  -- ' + detail) if detail else ''}")
    if not passed:
        FAILURES.append(name)


def a_print(meeting: str, speaker: str, seed: float) -> dict:
    return {
        "embedding": [seed] * 8,
        "meeting": meeting,
        "speaker": speaker,
        "added": "2026-01-01",
    }


TRANSCRIPT = """## Meeting 2026-01-0{n} 09:00 (10 min)

[00:00:01] {a}: Good morning.

[00:00:05] {b}: Morning. Did Anna send the file?

[00:00:09] {a}: Anna did, yes.

[00:00:12] SPEAKER_03: I can hear you both.
"""

NOTES = """# Kickoff

Present: [[{a}]], [[{b}]].

- **{a}** — send the deck to [[ {b} ]] by 2026-01-10
- {b} said Anna would review it.
"""


def a_meeting(root: Path, n: int, a: str, b: str, names: dict[str, str], *, synced: bool) -> Path:
    folder = root / f"2026-01-0{n}_0900"
    folder.mkdir(parents=True)
    (folder / paths.TRANSCRIPT_MD).write_text(TRANSCRIPT.format(n=n, a=a, b=b), encoding="utf-8")
    (folder / paths.NOTES_MD).write_text(NOTES.format(a=a, b=b), encoding="utf-8")
    speakers = {
        label_: {"embedding": [0.5 + i / 10] * 8, "snippets": [], "name": names.get(label_, "")}
        for i, label_ in enumerate(["SPEAKER_01", "SPEAKER_02", "SPEAKER_03"])
    }
    for entry in speakers.values():
        if not entry["name"]:
            del entry["name"]
    meta = {
        "id": folder.name,
        "started_at": f"2026-01-0{n}T09:00:00",
        "ended_at": f"2026-01-0{n}T09:10:00",
        "duration_seconds": 600,
        "status": "synced" if synced else "notes_written",
        "pauses": [],
        "audio": {},
        "transcription": {
            "channels": {"mic": {"speakers": speakers}},
        },
        "tags": [],
        "speaker_names": names,
        "referat_version": "0",
    }
    (folder / paths.META_JSON).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return folder


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="referat-person-"))
    print(f"scratch: {root}\n")
    meetings = root / "meetings"
    staging = root / "staging"
    voices_dir = root / "voices"
    meetings.mkdir()
    staging.mkdir()
    voices_dir.mkdir()

    # A version-1 database: three bare names, one of them two words.
    legacy = {
        "version": 1,
        "people": {
            "Niklas": [a_print("2026-01-01_0900", "SPEAKER_01", 0.1)],
            "Anna": [a_print("2026-01-01_0900", "SPEAKER_02", 0.2)],
            "Lars Klein": [a_print("2026-01-02_0900", "SPEAKER_02", 0.3)],
        },
    }
    (voices_dir / paths.VOICES_JSON).write_text(json.dumps(legacy), encoding="utf-8")

    base = load_config()
    config = replace(
        base,
        paths=replace(base.paths, meetings_dir=meetings, staging_dir=staging, voices_dir=voices_dir),
        speakers=replace(base.speakers, owner_name="Niklas"),
    )

    # Two meetings written before ids existed: `speaker_names` holds bare names.
    a_meeting(meetings, 1, "Niklas", "Anna", {"SPEAKER_01": "Niklas", "SPEAKER_02": "Anna"}, synced=True)
    a_meeting(meetings, 2, "Niklas", "Lars Klein", {"SPEAKER_01": "Niklas", "SPEAKER_02": "Lars Klein"}, synced=False)
    # And one with nobody named yet.
    a_meeting(meetings, 3, "SPEAKER_01", "SPEAKER_02", {}, synced=False)

    # --- 1. A version-1 file loads as records ---------------------------------
    print("=== a version-1 database ===")
    db = voices.VoicesDB.load(config)
    check("not flagged unreadable", not db.unreadable)
    check("ids are slugs of the names", sorted(db.people) == ["anna", "lars-klein", "niklas"], str(sorted(db.people)))
    lars = db.get("lars-klein")
    check("full and short name are both the legacy name", lars is not None and (lars.name, lars.short) == ("Lars Klein", "Lars Klein"))
    check("email is blank", lars is not None and lars.email == "")
    check("the file was not rewritten by reading it", json.loads((voices_dir / paths.VOICES_JSON).read_text())["version"] == 1)
    check("the owner resolves", (o := db.owner(config)) is not None and o.id == "niklas")
    check("display() renders a legacy name and an id alike", db.display("Lars Klein") == "Lars Klein" and db.display("lars-klein") == "Lars Klein")
    check("resolve() by short name, case-insensitively", db.resolve("anna")[0] is not None and db.resolve("anna")[0].id == "anna")
    check("resolve() of nobody is (None, '')", db.resolve("Zed") == (None, ""))

    # --- 2. The directory joins by id ------------------------------------------
    print("\n=== the directory ===")
    directory = {p.id: p for p in people.directory(config)}
    check("legacy speaker_names resolve to the record", directory["lars-klein"].appears_in == ["2026-01-02_0900"] and directory["lars-klein"].in_database)
    check("the owner is marked", directory["niklas"].is_owner and len(directory["niklas"].appears_in) == 2)
    check("gallery is ids", people.gallery(config, load_meetings(config)[0]) == ([], ["anna", "lars-klein", "niklas"]), str(people.gallery(config, load_meetings(config)[0])))
    doc = people_document(config)
    check("people --json carries id, short and email", all(k in doc["people"][0] for k in ("id", "short", "email")))
    check("and no embedding", "embedding" not in json.dumps(doc))

    # --- 3. Naming somebody new, with a short name --------------------------------
    print("\n=== naming a new person ===")
    ok, message = label.name_speaker(config, "2026-01-03_0900", "SPEAKER_01", "Anna Lind", "Anna")
    check("a second Anna is refused into a meeting? no - this meeting has no Anna yet", ok, message)
    db = voices.VoicesDB.load(config)
    lind = db.get("anna-lind")
    check("filed under a new id from the full name", lind is not None and lind.short == "Anna" and lind.name == "Anna Lind")
    check("the file is version 2 now", json.loads((voices_dir / paths.VOICES_JSON).read_text())["version"] == 2)
    m3 = next(m for m in load_meetings(config) if m.id == "2026-01-03_0900")
    check("speaker_names stores the id", m3.speaker_names.get("SPEAKER_01") == "anna-lind", str(m3.speaker_names))
    text = (meetings / "2026-01-03_0900" / paths.TRANSCRIPT_MD).read_text(encoding="utf-8")
    check("the transcript renders the short name", "[00:00:01] Anna: Good morning." in text)
    ok, message = label.name_speaker(config, "2026-01-03_0900", "SPEAKER_02", "anna")
    check("the other Anna is refused into the same meeting", not ok and "same short name" in message, message[:90])
    ok, message = label.name_speaker(config, "2026-01-03_0900", "SPEAKER_02", "Anna Lind", "Annie")
    check("a short name for somebody already on file is refused", not ok and "person rename" in message, message[:90])
    ok, message = label.name_speaker(config, "2026-01-03_0900", "SPEAKER_02", "Lars Klein")
    check("naming by full name resolves to the existing record", ok and "(lars-klein)" in message, message)
    ok, message = label.name_speaker(config, "2026-01-03_0900", "SPEAKER_03", "Zed Quill", "", "not an address")
    check("a bad email is refused", not ok and message.startswith("an email"), message[:80])
    check("and nobody was created by the refusal", voices.VoicesDB.load(config).resolve("Zed Quill") == (None, ""))

    # --- 4. A full-name collision gets -2 --------------------------------------------
    print("\n=== two people with one full name ===")
    db = voices.VoicesDB.load(config)
    second = db.new_person("Lars Klein", "Lasse", "lasse@example.org")
    check("the second Lars Klein is lars-klein-2", second.id == "lars-klein-2")
    db.save()
    db = voices.VoicesDB.load(config)
    person, why = db.resolve("Lars Klein")
    check("resolving the full name is now ambiguous, by id", person is None and "lars-klein, lars-klein-2" in why, why)
    person, why = db.resolve("Lasse")
    check("the short name still resolves", person is not None and person.id == "lars-klein-2")
    check("hotwords carry both spellings", {"Lars Klein", "Lasse", "Anna Lind", "Anna"} <= {t.term for t in hotwords.collect(config)})
    check("and never the email", not any("@" in t.term for t in hotwords.collect(config)))

    # --- 5. Renaming --------------------------------------------------------------------
    print("\n=== renaming ===")
    ok, message = label.rename_person(config, "lars-klein", short="Anna")
    check("a short name colliding inside a meeting is refused", not ok and "2026-01-03_0900" in message, message[:90])
    ok, message = label.rename_person(config, "lars-klein", short="Lars", email="lars@example.org")
    check("rename succeeds", ok, message)
    t2 = (meetings / "2026-01-02_0900" / paths.TRANSCRIPT_MD).read_text(encoding="utf-8")
    check("the legacy transcript is relabeled", "[00:00:05] Lars: Morning." in t2 and "Lars Klein:" not in t2)
    t3 = (meetings / "2026-01-03_0900" / paths.TRANSCRIPT_MD).read_text(encoding="utf-8")
    check("and the new one", "[00:00:05] Lars: Morning." in t3)
    n2 = (meetings / "2026-01-02_0900" / paths.NOTES_MD).read_text(encoding="utf-8")
    check("[[Wikilinks]] are rewritten, spaces inside the brackets included", "[[Lars]]" in n2 and "[[Lars Klein]]" not in n2 and "[[ Lars Klein ]]" not in n2, n2)
    check("prose is left alone", "Lars Klein said Anna would review it." in n2)
    m2 = next(m for m in load_meetings(config) if m.id == "2026-01-02_0900")
    check("speaker_names was rewritten to the id", m2.speaker_names.get("SPEAKER_02") == "lars-klein", str(m2.speaker_names))
    check("the per-channel name follows", m2.transcription["channels"]["mic"]["speakers"]["SPEAKER_02"]["name"] == "lars-klein")
    n1 = (meetings / "2026-01-01_0900" / paths.NOTES_MD).read_text(encoding="utf-8")
    check("a meeting the person is not in is untouched", "[[Anna]]" in n1)
    ok, message = label.rename_person(config, "anna", short="Annika")
    check("renaming a synced meeting's speaker", ok, message)
    m1 = next(m for m in load_meetings(config) if m.id == "2026-01-01_0900")
    check("drops it from synced to notes_written", m1.status is MeetingStatus.NOTES_WRITTEN, str(m1.status))
    check("shown with the id and the short name in the message", "anna is Anna, called Annika" in message, message)
    ok, message = label.rename_person(config, "anna", email="")
    check("a no-op rename says so", ok and "unchanged" in message, message)
    ok, message = label.rename_person(config, "anna")
    check("nothing to change is refused", not ok, message)
    ok, message = label.rename_person(config, "anna", name="ME")
    check("a reserved name is refused", not ok and "channel label" in message, message)
    doc = transcript_document(config, m1)
    check("transcript --json people is the rendered short name", "Annika" in doc["people"] and "Niklas" in doc["people"], str(doc["people"]))

    # --- 6. Forgetting by id, name and ambiguous name --------------------------------------
    print("\n=== forgetting ===")
    ok, message = label.forget_person(config, "Lars Klein")
    check("an ambiguous full name is refused by id", not ok and "lars-klein-2" in message, message)
    ok, message = label.forget_person(config, "lars-klein-2")
    check("forget by id", ok, message)
    ok, message = label.forget_person(config, "Lars")
    check("forget by short name", ok and "lars-klein" in message, message)
    t2 = (meetings / "2026-01-02_0900" / paths.TRANSCRIPT_MD).read_text(encoding="utf-8")
    check("labels revert", "[00:00:05] SPEAKER_02: Morning." in t2, t2[:120])
    m2 = next(m for m in load_meetings(config) if m.id == "2026-01-02_0900")
    check("speaker_names loses the id", "SPEAKER_02" not in m2.speaker_names)
    check("the person is out of the database", voices.VoicesDB.load(config).get("lars-klein") is None)
    ok, message = noise.mark_noise(config, "2026-01-01_0900", "SPEAKER_01")
    check("noise refuses a named cluster and names them through the database", not ok and "Niklas" in message and "--forget niklas" in message, message[:120])

    # --- 7. A collision on load is refused, not merged -------------------------------------
    print("\n=== a version-1 file with two names that slugify alike ===")
    clash = root / "clash"
    clash.mkdir()
    (clash / paths.VOICES_JSON).write_text(
        json.dumps({"version": 1, "people": {"Anna": [a_print("x", "y", 0.1)], "anna": [a_print("x", "z", 0.2)]}}),
        encoding="utf-8",
    )
    db = voices.VoicesDB.load(replace(config, paths=replace(config.paths, voices_dir=clash)))
    check("flagged unreadable", db.unreadable and db.people == {})

    shutil.rmtree(root, ignore_errors=True)
    print("\n" + "=" * 60)
    if FAILURES:
        print(f"{len(FAILURES)} miss(es): " + ", ".join(FAILURES))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
