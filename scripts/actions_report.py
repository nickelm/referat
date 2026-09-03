r"""Every action item this machine has, beside the line it was parsed from.

    .venv\Scripts\python.exe scripts\actions_report.py
    .venv\Scripts\python.exe scripts\actions_report.py --owners

There is no test suite here, so this is how :func:`referat.actions.parse_actions`
is checked: by reading its output against its input for the whole corpus at once.
`referat actions` prints the *result*; this prints the result next to the
evidence, which is the only way to see a wrong answer that looks reasonable.

Three things it is pointed at, because each was a bug or nearly one:

**The due date.** A date is shown only where a cue word makes it a deadline, and
the `DUE` column is the one to scan against the raw line. The rule this replaced
took any literal `YYYY-MM-DD` and got five of sixty-four wrong — every one of
them a date that was a *meeting*, a *start* or something explicitly deferred
past. Those five are named in :data:`referat.actions.CUE_DUE_RE`, and this is
what would catch the sixth.

**The qualifier.** Whatever the owner head said outside its bold spans. Almost
always empty; where it is not, it is carrying something the note has nowhere else
— `*(absent)*`, or who two external annotators report to — and an empty column
where the raw line plainly has a parenthetical is a silent deletion.

**Anything skipped.** A bullet the grammar could not take apart is counted at the
end rather than passed over, for the reason `parse_transcript` reports its
unparsed lines: a large number means the notes are no longer being written in the
shape this reads, which is a thing to find out from a report and not from a
dashboard that has quietly gone short.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from referat import actions, paths  # noqa: E402
from referat.config import load_config  # noqa: E402


def main(argv: list[str]) -> int:
    config = load_config()
    show_owners = "--owners" in argv

    total = skipped = with_due = with_qualifier = 0
    owners: dict[str, int] = {}

    for root in config.meeting_roots():
        for folder in paths.list_meeting_dirs(root):
            notes = folder / paths.NOTES_MD
            if not notes.exists():
                continue
            text = notes.read_text(encoding="utf-8")
            items = actions.parse_actions(text, folder.name)
            # Count the bullets the section held, so a skipped one is visible as
            # a gap rather than simply never mentioned.
            bullets = len(actions._bullets(actions._section(text)))
            skipped += bullets - len(items)
            if not items:
                continue

            print(f"\n=== {folder.name}  ({len(items)} of {bullets} bullets) ===")
            for item in items:
                total += 1
                with_due += bool(item.due)
                with_qualifier += bool(item.qualifier)
                for name in item.owners or [actions.UNASSIGNED]:
                    owners[name] = owners.get(name, 0) + 1

                print(f"  raw   {item.raw}")
                print(f"  ->    key={item.key}  due={item.due or '-'}  at={item.at or '-'}")
                print(f"        owners={item.owners or [actions.UNASSIGNED]}")
                if item.qualifier:
                    print(f"        qualifier={item.qualifier!r}")
                print(f"        text={item.text}")
                print()

    print("=" * 72)
    print(f"{total} items | {with_due} with a due date | {with_qualifier} with a qualifier")
    if skipped:
        # Loud, and never a bare number in a corner. A bullet this grammar cannot
        # read is an action item nothing will ever raise again.
        print(f"!! {skipped} bullet(s) under a `## Action items` heading did not parse")
    else:
        print("every bullet under every `## Action items` heading parsed")

    if show_owners:
        print()
        for name, count in sorted(owners.items(), key=lambda p: (-p[1], p[0])):
            print(f"{count:4}  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
