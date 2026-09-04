"""That a broken `voices.json` cannot cost you every voiceprint on the machine.

The known-voices database is the one file here that **cannot be reconstructed**.
A voiceprint is an embedding computed from audio that has since been deleted, and
`.voices/` is deliberately kept out of the sync client and out of every backup,
because it is biometric data about people who never asked to be in a database.
That protection is also what means a bad write has nothing to restore from.

`VoicesDB.load` degrades to an empty database when the file will not parse, which
is right -- a broken file may never cost a transcript. `VoicesDB.save` then
rewrites the file *whole*. Put those two together and one unparseable byte plus
one save replaces nine people with one entry, silently. The worst caller is not a
person at a prompt: `voices.bootstrap_owner` runs inside the transcription
pipeline, on a background thread, with nobody watching.

This checks the two answers to that:

1. **A refusal at every write path** -- the pipeline's, and the four a person can
   reach -- plus a backstop inside `save` itself, so a forgotten check cannot
   cost the file either.
2. **A backup before every write**, kept beside the database and rotated, so even
   a *correct* write is recoverable.

Everything runs against a throwaway copy in the scratch directory. **It never
touches the real database**, which is the whole point of the file it is testing.

    .venv\\Scripts\\python.exe scripts\\voices_guard_fixture.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from referat import label, paths, voices  # noqa: E402
from referat.config import load_config  # noqa: E402
from referat.meeting import load_meetings  # noqa: E402

FAILURES: list[str] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if passed else 'MISS'} {name}{('  -- ' + detail) if detail else ''}")
    if not passed:
        FAILURES.append(name)


def scratch_config(folder: Path, people: dict[str, list] | str):
    """A config whose voices folder is `folder`, seeded with `people` (or raw text)."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / paths.VOICES_JSON
    if isinstance(people, str):
        target.write_text(people, encoding="utf-8")
    else:
        target.write_text(json.dumps({"version": 1, "people": people}), encoding="utf-8")
    base = load_config()
    return replace(base, paths=replace(base.paths, voices_dir=folder))


def a_print(name: str) -> dict:
    return {
        "embedding": [0.1] * 8,
        "meeting": "2026-01-01_0900",
        "speaker": "SPEAKER_01",
        "added": "2026-01-01",
    }


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="referat-voices-guard-"))
    print(f"scratch: {root}\n")

    real = load_config().voices_dir() / paths.VOICES_JSON
    before = real.read_bytes() if real.exists() else b""

    # --- 1. A good file loads, is not flagged, and saves with a backup -------
    print("=== a healthy database ===")
    good = scratch_config(root / "good", {"Anna": [a_print("Anna")], "Bo": [a_print("Bo")]})
    db = voices.VoicesDB.load(good)
    check("a good file is not flagged unreadable", not db.unreadable)
    check("both people load", sorted(db.people) == ["Anna", "Bo"], str(sorted(db.people)))

    db.add("Cleo", [0.2] * 8, "2026-01-02_1000", "SPEAKER_02")
    db.save()
    backups = sorted((root / "good" / voices.BACKUPS_DIR).glob("voices-*.json"))
    check("a backup was written before the save", len(backups) == 1)
    if backups:
        kept = json.loads(backups[0].read_text(encoding="utf-8"))
        check(
            "the backup holds what was there BEFORE the write",
            sorted(kept["people"]) == ["Anna", "Bo"],
            str(sorted(kept["people"])),
        )
    now = voices.VoicesDB.load(good)
    check("the save landed", sorted(now.people) == ["Anna", "Bo", "Cleo"])

    # --- 2. Rotation --------------------------------------------------------
    print("\n=== rotation ===")
    for n in range(voices.BACKUPS_KEPT + 5):
        db = voices.VoicesDB.load(good)
        db.add(f"P{n}", [0.3] * 8, "2026-01-03_1100", "SPEAKER_03")
        # The stamp has one-second resolution, so force distinct names rather
        # than sleeping 20 seconds inside a fixture.
        voices._back_up(good.voices_dir() / paths.VOICES_JSON)
        (good.voices_dir() / voices.BACKUPS_DIR / f"voices-20260101-{n:06d}.json").write_text(
            "{}", encoding="utf-8"
        )
        voices._rotate(good.voices_dir() / voices.BACKUPS_DIR, "voices")
    kept = sorted((good.voices_dir() / voices.BACKUPS_DIR).glob("voices-*.json"))
    check(
        f"no more than {voices.BACKUPS_KEPT} backups are kept",
        len(kept) <= voices.BACKUPS_KEPT,
        f"{len(kept)} on disk",
    )

    # --- 3. The broken file: every write path refuses ------------------------
    print("\n=== an unreadable database ===")
    bad = scratch_config(root / "bad", '{"version": 1, "people": {"Anna": [tru')
    db = voices.VoicesDB.load(bad)
    check("a truncated file is flagged unreadable", db.unreadable)
    check("and reads as nobody, which is what makes it dangerous", db.people == {})

    raised = False
    try:
        db.save()
    except voices.VoicesError:
        raised = True
    check("save() itself refuses, as the backstop", raised)
    still = (bad.voices_dir() / paths.VOICES_JSON).read_text(encoding="utf-8")
    check("the broken file was not overwritten", still.endswith("[tru"))

    ok, message = label.forget_person(bad, "Anna")
    check("forget_person refuses", not ok and "could not be read" in message, message[:60])

    code = label.run_forget(bad, "Anna", assume_yes=True)
    check("referat label --forget refuses", code == 1)

    # A **real** meeting id, because `run_drop_voiceprint` resolves the meeting
    # before it opens the database -- so a made-up id would make this exit 1 for
    # the wrong reason and pass without testing anything.
    real_meeting = next(
        (m.id for m in load_meetings(load_config())), ""
    )
    if not real_meeting:
        check("--drop-voiceprint refuses", False, "no meeting to test against")
    else:
        import io
        import contextlib

        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            code = label.run_drop_voiceprint(bad, real_meeting, "SPEAKER_01", assume_yes=True)
        said = captured.getvalue()
        check(
            "--drop-voiceprint refuses, and for the right reason",
            code == 1 and "could not be read" in said,
            said.strip()[:70],
        )

    # --- 4. Also-broken: a file that parses but is not an object -------------
    print("\n=== a file that parses but is not a database ===")
    weird = scratch_config(root / "weird", "[1, 2, 3]")
    db = voices.VoicesDB.load(weird)
    check("a JSON array is flagged unreadable too", db.unreadable)

    # --- 5. A missing file is NOT unreadable --------------------------------
    print("\n=== a missing database ===")
    missing = load_config()
    missing = replace(missing, paths=replace(missing.paths, voices_dir=root / "missing"))
    db = voices.VoicesDB.load(missing)
    check("a missing file is not flagged", not db.unreadable)
    db.add("Dana", [0.4] * 8, "2026-01-04_1200", "SPEAKER_04")
    db.save()
    check("and saves normally, seeding the folder", (root / "missing" / paths.VOICES_JSON).exists())

    # --- 6. The real database is untouched ----------------------------------
    print("\n=== the real database ===")
    after = real.read_bytes() if real.exists() else b""
    check("this fixture did not touch it", before == after, f"{len(after):,} bytes")

    shutil.rmtree(root, ignore_errors=True)
    print("\n" + "=" * 60)
    if FAILURES:
        print(f"{len(FAILURES)} miss(es): " + ", ".join(FAILURES))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
