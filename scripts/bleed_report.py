r"""What speaker bleed looks like in a transcript that has already been written.

    .venv\Scripts\python.exe scripts\bleed_report.py 2026-09-02_1059

The pipeline's suppression needs both channels' segments and can only ever run
during transcription. This is the other half: it reads a *rendered*
`transcript.md` plus its `meta.json` and reports what a text-and-time rule could
still find there, which is the only thing available for a meeting whose audio has
been released.

It exists because these thresholds will be revisited and the next person needs
the same numbers. Two of them in particular:

**The disjoint-pair count** across a grid of `min_tokens`, `contain`, `window`
and `back_contain` — how many entries a repair would actually remove.

**The room-to-room false-pairing count**, which is the calibration signal and the
reason the grid is printed rather than a single number. A rendered transcript has
no channel column, so the only structural handle left is that some names resolve
to clusters on *both* channels and some to the microphone alone; a pairing
between two names that only ever appeared on the microphone cannot be echo, and
is therefore a measurement of the rule's error rate rather than of its yield.
That is what caught one-directional containment being unusable: 21 such pairings
against 8 once the reverse direction was required.
"""

from __future__ import annotations

import sys
from pathlib import Path

from referat.bleed import duplicates, tokens
from referat.config import BleedConfig, load_config
from referat.meeting import resolve_meeting
from referat.transcribe import parse_entry

GRID = [
    # (min_tokens, contain, window, back_contain) - back_contain 0.0 is "off"
    (3, 0.60, 20.0, 0.0),
    (5, 0.60, 20.0, 0.0),
    (5, 0.60, 20.0, 0.50),
    (5, 0.60, 6.0, 0.50),
    (5, 0.70, 6.0, 0.50),
]


def channels_of(meeting) -> dict[str, set[str]]:
    """Every name in `meta.json`, mapped to the channels its clusters came out of.

    The one piece of provenance a rendered transcript still has. A name resolving
    to clusters on both channels was recorded twice; a name on the microphone
    alone is somebody in the room, and two of those pairing with each other is an
    error rather than a find.
    """
    out: dict[str, set[str]] = {}
    for channel, entry in (meeting.transcription.get("channels") or {}).items():
        if not isinstance(entry, dict):
            continue
        for label in entry.get("speakers") or {}:
            name = meeting.speaker_names.get(label)
            if name:
                out.setdefault(name, set()).add(str(channel))
    return out


def pairs(entries, settings: BleedConfig, both: set[str]) -> tuple[list, int]:
    """Disjoint duplicate pairs, and how many of them pair two room-only names.

    Greedy and disjoint: once an entry has been paired it is neither removed
    twice nor used to justify a second removal. The kept copy is the one whose
    tokens contain the other's — never the better-punctuated one, which was
    measured across 67 pairs and came out 24 better, 20 worse, 23 tied, i.e. a
    coin flip.
    """
    found, used, wrong = [], set(), 0
    for i, (at, label, text) in enumerate(entries):
        if i in used:
            continue
        for j in range(i + 1, len(entries)):
            if j in used:
                continue
            other_at, other_label, other_text = entries[j]
            if other_at - at > settings.window:
                break
            if not duplicates(text, other_text, settings):
                continue
            # Keep whichever copy contains the other; on a tie, the longer.
            a, b = tokens(text), tokens(other_text)
            keep_first = len(a) >= len(b)
            found.append((i, j) if keep_first else (j, i))
            used.update({i, j})
            if label not in both and other_label not in both:
                wrong += 1
            break
    return found, wrong


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    config = load_config()
    meeting, complaint = resolve_meeting(config, argv[0])
    if meeting is None:
        print(complaint, file=sys.stderr)
        return 1

    path: Path = meeting.transcript_path
    lines = path.read_text(encoding="utf-8").split("\n")
    entries = [parsed for line in lines if (parsed := parse_entry(line))]
    if not entries:
        print(f"{meeting.id}: nothing in {path.name} parses as an entry")
        return 1

    by_name: dict[str, int] = {}
    for _at, label, _text in entries:
        by_name[label] = by_name.get(label, 0) + 1
    resolved = channels_of(meeting)
    both = {name for name, channels in resolved.items() if len(channels) > 1}

    print(f"\n{meeting.id}: {len(entries)} entries in {path.name}\n")
    print("  entries per label")
    for label, n in sorted(by_name.items(), key=lambda kv: -kv[1]):
        where = "/".join(sorted(resolved.get(label, {"-"})))
        mark = "  <- both channels" if label in both else ""
        print(f"    {label:<12} {n:5d}  {where}{mark}")

    short = sum(1 for _at, label, text in entries if label in both and len(tokens(text)) < 5)
    print(f"\n  {sum(by_name[n] for n in both)} entries under a two-channel name")
    print(f"  {short} of them are under 5 tokens and no text rule may ever touch them\n")

    print(
        f"  {'min_tok':>7} {'contain':>7} {'window':>7} {'back':>5} "
        f"{'pairs':>6} {'room<->room':>12}"
    )
    for min_tokens, contain, window, back in GRID:
        settings = BleedConfig(
            min_tokens=min_tokens, contain=contain, window=window, back_contain=back
        )
        found, wrong = pairs(entries, settings, both)
        print(
            f"  {min_tokens:>7} {contain:>7.2f} {window:>7.1f} "
            f"{back if back else 'off':>5} {len(found):>6} {wrong:>12}"
        )
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
