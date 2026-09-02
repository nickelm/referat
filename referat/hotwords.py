r"""The one global hotword list handed to faster-whisper.

**This is the only correction that happens before the transcript exists.**
Everything else in Referat corrects the *notes*, because `transcript.md` is
immutable and keeps whatever Whisper heard. A hotword is the chance not to need
the correction at all: a name the model has been told about is heard right the
first time. It costs nothing at transcription time — the terms go into Whisper's
prompt, not through a second model.

**Three sources, one list, one machine.** `[transcription].hotword_extras` for
the terms belonging to no project and to no person, every name in the
known-voices database, and every project's `glossary` from `projects.json`. It is
never one list per project, because a meeting is tagged *after* it has been
transcribed — at the moment the model runs there is nothing to select on, and
both alternatives that would create something to select on break a rule that
matters more: inferring a project from the transcript, or making the path from
stop to transcript wait for a human being.

**It reads live and derives no path of its own.** The database comes through
:meth:`referat.config.Config.voices_dir` and the projects file through
:mod:`referat.projects`, which is the rule that keeps the voiceprints from
drifting back into a synced folder. Reading rather than caching is also what
makes `referat label --forget <name>` take that name out of this list along with
the person — the privacy posture would otherwise leak out through the back of the
transcription stack.

**Nothing here may cost a transcript.** :func:`merge` returns `[]` on any
failure — an unreadable `projects.json`, a missing voices database, a path that
will not resolve — exactly as :func:`referat.diarize.diarize` and
:func:`referat.voices.identify` do. A meeting transcribed without hotwords is a
meeting with a few more misheard names in it; a meeting that raised here is no
meeting at all.

`[speakers].owner_name` is deliberately **not** a fourth source. The owner
reaches this list the moment :func:`referat.voices.bootstrap_owner` files their
first voiceprint under that name; until then their name belongs in
`hotword_extras` like anybody else's term.

Light on purpose — two JSON reads and some string handling, so `referat hotwords`
runs without the `transcribe` extra. That is also why the cap below counts tokens
by estimate rather than by loading Whisper's tokenizer.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from referat import projects, voices

if TYPE_CHECKING:
    from referat.config import Config

log = logging.getLogger(__name__)

SEPARATOR = ", "
"""What the terms are joined with on the way into Whisper's prompt."""

TOKEN_BUDGET = 223
r"""How many tokens the hotword prompt may use, measured rather than guessed.

faster-whisper 1.2.1 builds the prompt in `WhisperModel.generate_with_fallback`:

    hotwords_tokens = tokenizer.encode(" " + hotwords.strip())
    if len(hotwords_tokens) >= self.max_length // 2:
        hotwords_tokens = hotwords_tokens[: self.max_length // 2 - 1]

with `self.max_length = 448`. So the real budget is 223 tokens and the real
overflow behaviour is a slice — somebody else's rule cutting at somebody else's
boundary, mid-token and mid-name. Capping here instead means the list is cut
between whole terms, in a priority order this file chooses, and says what it
dropped.
"""

CONFIG_SOURCE = "config"
VOICES_SOURCE = "voices"
"""What a term's `source` says when it came from `hotword_extras` or from a name.

A glossary term reports its project's id instead, which is what makes `referat
hotwords` able to say where a term came from without a second lookup.
"""

_CHARS_PER_TOKEN = 2.0
_TOKENS_PER_TERM = 2
"""The estimate: `ceil(len(term) / 2) + 2`, measured rather than reasoned about.

There is no tokenizer to ask — this module runs without the `transcribe` extra —
so the cost of a term is estimated from its length, and the estimate has to err
*upwards*. Over-estimating drops a term or two off the bottom of a fixed priority
order and says so; under-estimating hands faster-whisper a list it slices at
token 223, mid-name, silently.

One character per two tokens looks absurdly pessimistic against the four-per-token
Whisper's BPE gets on English prose, and it is not, because a hotword list is
made of the material BPE is worst at: surnames, hyphenated compounds, internal
capitals, digits. Checked against the real large-v3 tokenizer on eight lists —
this machine's own names, people and jargon, acronyms, plain first names, long
phrases, and 40 hyphenated `Synthetic-Term-003`-shaped strings, which is the
worst case — the estimate came out between 1.10x and 2.04x the true count and
never once under it. `ceil(len / 3) + 1` was tried first and was under by 30% on
that worst case, which is the whole reason this is a measured constant.
"""


@dataclass(frozen=True)
class Term:
    """One hotword and where it came from."""

    term: str
    source: str
    """`config`, `voices`, or the id of the project whose glossary carried it."""


def estimate_tokens(term: str) -> int:
    """How many prompt tokens `term` is assumed to cost, separator included.

    An estimate rather than a count, and deliberately a high one — see
    :data:`_CHARS_PER_TOKEN`. Floored at two, since even a one-character term
    costs itself and the ``, `` before it.
    """
    return max(2, math.ceil(len(term) / _CHARS_PER_TOKEN) + _TOKENS_PER_TERM)


def collect(config: Config) -> list[Term]:
    """Every hotword this machine knows, in priority order and deduplicated.

    The order is fixed — `hotword_extras`, then the known-voices names, then the
    project glossaries by project id — so that two runs over the same meeting
    build the same prompt, and so that :func:`cap` drops from a predictable end.
    Deduplication is case-insensitive and the first occurrence wins, which means a
    term is attributed to the highest-priority source carrying it.

    Never raises; see the module docstring.
    """
    terms: list[Term] = []
    seen: set[str] = set()

    def take(term: str, source: str) -> None:
        cleaned = " ".join(str(term).split())
        key = cleaned.casefold()
        if not cleaned or key in seen:
            return
        seen.add(key)
        terms.append(Term(term=cleaned, source=source))

    try:
        for extra in config.transcription.hotword_extras:
            take(extra, CONFIG_SOURCE)
        # Read live rather than cached, so a forgotten person leaves the list
        # with their voiceprints.
        for name in voices.VoicesDB.load(config).names():
            take(name, VOICES_SOURCE)
        # `ordered()` is by id, which is fixed at creation — so a rename cannot
        # reshuffle the prompt.
        for project in projects.ProjectsDB.load(config).ordered():
            for word in project.glossary:
                take(word, project.id)
    except Exception:
        log.warning("cannot build the hotword list; transcribing without one", exc_info=True)
        return []
    return terms


def cap(terms: list[Term]) -> tuple[list[Term], list[Term]]:
    """Split `terms` into what fits in :data:`TOKEN_BUDGET` and what does not.

    In the order given, which :func:`collect` has already made the priority
    order. A term that does not fit is dropped and the walk continues, so one
    very long glossary entry cannot cost every shorter term behind it.
    """
    kept: list[Term] = []
    dropped: list[Term] = []
    used = 0
    for term in terms:
        cost = estimate_tokens(term.term)
        if used + cost > TOKEN_BUDGET:
            dropped.append(term)
            continue
        used += cost
        kept.append(term)
    return kept, dropped


def merge(config: Config) -> list[str]:
    """The hotword list, capped — the function the pipeline and the CLI both call.

    Logs whatever the cap dropped, by name. A cap nobody can see is how this turns
    into a bug report about one specific name that is never heard right.
    """
    kept, dropped = cap(collect(config))
    if dropped:
        log.info(
            "hotwords: %d term(s) over the %d-token budget were dropped: %s",
            len(dropped),
            TOKEN_BUDGET,
            ", ".join(t.term for t in dropped),
        )
    return [t.term for t in kept]


def prompt(config: Config) -> str:
    """The merged list as the single string faster-whisper's `hotwords` takes.

    Empty when there is nothing to say, which callers pass as `None` rather than
    as an empty prompt.
    """
    return SEPARATOR.join(merge(config))