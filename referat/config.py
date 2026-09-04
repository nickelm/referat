"""Loading and validating `config.toml`.

Read-only: Referat parses TOML with the stdlib `tomllib` and never writes it
back, so hand edits and comments in the file survive untouched. The only write
is the first-run copy of `config.example.toml`.
"""

from __future__ import annotations

import logging
import shutil
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

from referat import paths

log = logging.getLogger(__name__)

VALID_DEVICES = ("auto", "cuda", "cpu")
VALID_COMPUTE_TYPES = ("auto", "float16", "float32", "int8", "int8_float16")
VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class ConfigError(Exception):
    """Raised when config.toml is present but unusable."""


@dataclass(frozen=True)
class HotkeysConfig:
    toggle_record: str = "ctrl+alt+f9"
    toggle_pause: str = "ctrl+alt+f10"


@dataclass(frozen=True)
class AudioConfig:
    mic_device: str = ""
    loopback_device: str = ""
    mic_samplerate: int = 16000


@dataclass(frozen=True)
class PathsConfig:
    meetings_dir: Path = Path("~/Meetings")
    hf_token_file: Path = Path("~/.referat/hf_token")
    staging_dir: Path | None = None
    r"""Where a meeting is recorded and transcribed. Unset means
    `%LOCALAPPDATA%\Referat\recording`.

    Recording never writes into the meetings folder, and the meeting only moves
    there once its WAVs are gone — see :meth:`Config.staging_dir`.
    """
    voices_dir: Path | None = None
    """Where the known-voices database lives. Unset means `<meetings_dir>/.voices`.

    Set it when the meetings folder is inside Dropbox, OneDrive or any other sync
    client. The transcripts and notes are worth syncing; the voiceprints are
    biometric data about people who never asked to be in a database, and they must
    stay on this machine. Pointing this somewhere local keeps that true by
    configuration, rather than by a per-folder sync exclusion that nobody will
    remember to re-apply after the folder is recreated.
    """


@dataclass(frozen=True)
class TranscriptionConfig:
    model: str = "large-v3"
    cpu_fallback_model: str = "medium"
    device: str = "auto"
    compute_type: str = "auto"
    languages: tuple[str, ...] = ("en", "sv")
    """The languages this machine is expected to hear, most likely first.

    One key with three meanings, and each is a real state rather than a mode
    flag. **One code** pins that language and asks Whisper nothing -- the
    cheapest and the most certain, and what this was until 2026-09-04. **Two or
    more** run a detection pass per channel and take the best *of these*, which
    is the case this machine is actually in: meetings are held in English and in
    Swedish, and which one is not known until somebody speaks. **Empty** is bare
    autodetect over all ninety-nine languages Whisper knows.

    The middle state is the point, and it is not the same thing as the empty one.
    Swedish sits in a dense neighbourhood -- Norwegian, Danish, German, Dutch --
    and an unrestricted guess off a quiet or short channel lands in it often
    enough to matter, which costs the whole channel: a language is chosen once
    and every segment is decoded under it. Naming the candidates makes a wrong
    answer require beating a *plausible* rival rather than merely being the
    loudest of a hundred, and a code that never gets said costs nothing but the
    room it takes on this line.

    Order is not preference and nothing breaks ties by it -- probability decides.
    It is the order :func:`referat.transcribe.detect_language` logs in, so the
    first entry should be the ordinary case if only to make the log read right.

    Detection is per **channel**, not per meeting: the microphone is the room and
    the loopback is the far end, and a Swedish room on an English call is exactly
    the meeting this exists for. It is never per *segment* -- faster-whisper's
    `multilingual=True` re-detects on every one of them, which turns one wrong
    guess per channel into one wrong guess anywhere and makes a transcript that
    switches language mid-sentence.
    """
    diarization: bool = True
    diarization_model: str = "pyannote/speaker-diarization-community-1"
    keep_audio: bool = False
    """Keep the WAVs even when the quality gate accepts the transcript.

    The pipeline's default is to delete them the moment every channel comes out
    clean, which is right: they are recordings of people who never asked to be
    recorded, and `audio_released` is a promise. But the audio is also the only
    material that can ever calibrate `[speakers].match_threshold` and
    `match_margin` against real voices, and once it is gone no `rerun` brings it
    back. Turn this on for a meeting worth keeping, and turn it off again.

    A meeting kept this way is **not** `gate_failed` — the gate is still asked,
    and still writes that status when the transcript looks bad. It stays
    `transcribed` and stays in the staging folder, because no WAV may ever reach
    the meetings folder; `referat promote <id> --release-audio` is the off-ramp
    once the audio has served its purpose."""
    hotword_extras: tuple[str, ...] = ()
    """Terms belonging to no project and to no person, for the global hotword list.

    The third and smallest source :func:`referat.hotwords.merge` draws on -- the
    other two are every name in the known-voices database and every project's
    `glossary`, both of which maintain themselves. Put here what neither of those
    will ever hold: an acronym, a building, a piece of jargon.

    A tuple rather than a list because every section here is frozen; TOML writes
    it as an array of strings and :func:`_coerce` converts it.
    """


@dataclass(frozen=True)
class SpeakersConfig:
    """Putting names on the numbers diarization produced. See :mod:`referat.voices`.

    The two thresholds are the only place a wrong name can get written into a
    transcript, so they start strict and are loosened only against real voices.
    """

    identify: bool = True
    owner_name: str = ""
    """Who the microphone channel is. Empty until you fill it in by hand — Referat
    never writes this file back — and the automatic mic-channel additions to the
    known-voices database start from the moment you do."""
    match_threshold: float = 0.70
    """Cosine similarity a cluster must reach before it is called by a name."""
    match_margin: float = 0.15
    """...and by how much it must beat the runner-up *name*. Both, or the speaker
    stays `SPEAKER_NN`: putting the wrong name on someone's words is worse than
    leaving a number."""
    snippets_per_speaker: int = 3
    snippet_seconds: float = 6.0
    """How much audio `referat label` gets to play back per unknown speaker.
    Three six-second clips of 16 kHz mono is under 600 KB."""


@dataclass(frozen=True)
class BleedConfig:
    """Removing the microphone's copy of speech that also arrived on the loopback.

    See :mod:`referat.bleed`. These are knobs against the rule elsewhere in this
    project that knobs nobody asked for do not get added, and they earn it on the
    same grounds `match_threshold` and `match_margin` did: this is the only place
    a rule **deletes lines from a transcript**, and the numbers can only be
    calibrated against real hybrid meetings, of which exactly one exists and its
    audio has been released.

    So the defaults below are **reasoned from a rendered transcript, not measured
    against audio**. `contain`, `back_contain` and `min_tokens` were fitted to
    `2026-09-02_1059` and have a false-pairing count behind them; `cluster_time`
    and `cluster_text` have nothing of the kind and cannot until the next hybrid
    meeting is recorded with `[transcription].keep_audio` on.
    """

    suppress: bool = True
    window: float = 6.0
    """Seconds of tolerance when deciding two segments describe one moment.

    An interval overlap, not a gap between start times: two copies of one
    utterance were measured a median of 2 seconds apart but as much as 34, and the
    long ones are where one channel emitted a run-on segment covering what the
    other split up."""
    contain: float = 0.60
    """How much of the microphone line's text the loopback line must contain."""
    back_contain: float = 0.50
    """...and how much of the loopback line's text the microphone line must contain.

    Not symmetry for its own sake. Without the reverse direction a short line is
    swallowed by any longer line nearby holding its words: forward-only produced
    21 pairings of one room speaker against another on the meeting this was built
    for, and requiring the reverse cut that to 8."""
    min_tokens: int = 5
    """Nothing shorter is ever dropped by text.

    More than half of a hybrid meeting is one- and two-token backchannels, and
    nothing distinguishes a remote person's second "Yeah" from the microphone's
    copy of their first. Those are removed only when the cluster rule takes the
    whole cluster, which decides by whose voice they are rather than by what they
    say."""
    cluster_time: float = 0.80
    cluster_text: float = 0.50
    """A whole microphone cluster is echo when this much of its speech time sits
    under loopback speech *and* this much of its long-enough lines matches loopback
    text. Both: a microphone in a room is voiced almost continuously — 97% of the
    meeting this was built for — so time alone convicts anyone who talks while the
    far end is talking."""


@dataclass(frozen=True)
class CleanupConfig:
    """Running `/cleanup`, which is the only LLM work in this project."""

    claude_binary: str = ""
    """Where the `claude` binary is, when it cannot be found on its own.

    Empty by default and normally left that way: :func:`referat.notes.resolve_claude`
    reads the installed Claude Code VS Code extension and honours the `.obsolete`
    file beside it, then falls back to `PATH`. This is the escape hatch for a
    machine where neither works.

    **Not a credential and never one.** `claude` owns all authentication; no
    Anthropic API key belongs in this file, in the environment, or anywhere in
    this project — see `CLAUDE.md`'s Conventions.
    """


@dataclass(frozen=True)
class AppConfig:
    log_level: str = "INFO"


@dataclass(frozen=True)
class Config:
    hotkeys: HotkeysConfig = field(default_factory=HotkeysConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    speakers: SpeakersConfig = field(default_factory=SpeakersConfig)
    bleed: BleedConfig = field(default_factory=BleedConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    app: AppConfig = field(default_factory=AppConfig)
    source: Path | None = None
    """The file this config was read from, or None for pure defaults."""

    def hf_token(self) -> str | None:
        """The Hugging Face token, or None when the token file is missing or empty.

        Diarization needs it; everything else works without. Callers degrade to
        undifferentiated REMOTE labels rather than failing.

        Read as bytes and decoded leniently, because the encoding is whatever
        wrote the file. `Set-Content` and `>` in Windows PowerShell 5.1 produce
        UTF-16 with a BOM, which is how this file was in fact first created
        here; Notepad produces UTF-8 with one. A token is ASCII either way, so
        the BOM decides and anything undecodable is dropped rather than raising
        into a transcription job.
        """
        try:
            raw = self.paths.hf_token_file.read_bytes()
        except OSError:
            return None
        encoding = "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
        return raw.decode(encoding, errors="ignore").strip() or None

    def voices_dir(self) -> Path:
        """The known-voices folder: `[paths].voices_dir`, or `<meetings_dir>/.voices`.

        Resolved here rather than at each call site so there is exactly one answer
        to where the voiceprints are — see :attr:`PathsConfig.voices_dir` for why
        the override exists.
        """
        return paths.voices_dir(self.paths.meetings_dir, self.paths.voices_dir)

    def staging_dir(self) -> Path:
        """Where meetings are recorded and transcribed, before their audio is gone.

        **The WAVs must never be written into a folder anything syncs.** They are
        ~460 MB an hour and are deleted once the transcript is judged trustworthy,
        but a sync client would upload every one of them on the way past and then
        keep the "deleted" audio in its own trash and version history for weeks —
        recordings of people who never asked to be recorded, on somebody else's
        servers, after Referat reported them gone. Syncing a file that is still
        being appended to is its own hazard besides.

        So a meeting is recorded here, transcribed here, and moved into
        `meetings_dir` by :func:`referat.paths.move_meeting_dir` only once
        `release_audio_if_clean` has deleted the WAVs. A meeting whose audio was
        kept stays here, and `referat list` still shows it.
        """
        return self.paths.staging_dir or paths.default_staging_dir()

    def meeting_roots(self) -> list[Path]:
        """Every folder a meeting may be in, meetings folder first.

        Anything that lists meetings or resolves an id has to look in both, or a
        meeting that kept its audio becomes invisible to `list`, `label` and
        `rerun` — which are exactly the commands that would fix it.
        """
        roots = [self.paths.meetings_dir, self.staging_dir()]
        return list(dict.fromkeys(roots))


_SECTIONS: dict[str, type] = {
    "hotkeys": HotkeysConfig,
    "audio": AudioConfig,
    "paths": PathsConfig,
    "transcription": TranscriptionConfig,
    "speakers": SpeakersConfig,
    "bleed": BleedConfig,
    "cleanup": CleanupConfig,
    "app": AppConfig,
}


def ensure_config_file(path: Path | None = None) -> Path:
    """Create `config.toml` from the packaged example if it does not exist yet."""
    target = path or paths.config_path()
    if target.exists():
        return target
    if not paths.EXAMPLE_CONFIG_PATH.exists():
        raise ConfigError(f"missing template {paths.EXAMPLE_CONFIG_PATH}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(paths.EXAMPLE_CONFIG_PATH, target)
    log.info("created %s from config.example.toml", target)
    return target


def load_config(path: Path | None = None, *, bootstrap: bool = True) -> Config:
    """Load the config, creating it and the meetings folder on first run.

    Unknown keys are warned about and ignored, so a config written by a newer
    version of Referat still loads.
    """
    target = path or paths.config_path()
    if bootstrap:
        ensure_config_file(target)

    raw: dict[str, Any] = {}
    if target.exists():
        try:
            with target.open("rb") as fh:
                raw = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"cannot read {target}: {exc}") from exc
    else:
        log.warning("no config at %s; using defaults", target)
        target = None  # type: ignore[assignment]

    for key in raw:
        if key not in _SECTIONS:
            log.warning("ignoring unknown config section [%s]", key)

    sections = {name: _build(cls, raw.get(name, {}), name) for name, cls in _SECTIONS.items()}
    config = Config(source=target, **sections)  # type: ignore[arg-type]
    _validate(config)
    if bootstrap:
        ensure_meetings_dir(config)
    return config


def _build(cls: type, values: dict[str, Any], section: str) -> Any:
    """Instantiate a section dataclass from raw TOML values, coercing by field type."""
    assert is_dataclass(cls)
    known = {f.name: f for f in fields(cls)}
    kwargs: dict[str, Any] = {}
    for key, value in values.items():
        spec = known.get(key)
        if spec is None:
            log.warning("ignoring unknown config key [%s].%s", section, key)
            continue
        kwargs[key] = _coerce(value, spec.type, section, key)
    return cls(**kwargs)


def _coerce(value: Any, declared: Any, section: str, key: str) -> Any:
    """Coerce a TOML scalar to the field's declared type. Only Path needs work."""
    if declared in (Path, "Path", "Path | None"):
        if not isinstance(value, str):
            raise ConfigError(f"[{section}].{key} must be a path string")
        return Path(value).expanduser()
    if declared in ("tuple[str, ...]",):
        # Refused at load rather than shrugged off later: a bare string here is
        # the natural mistake, and Python would happily iterate it into one
        # hotword per character.
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"[{section}].{key} must be a list of strings")
        return tuple(term for v in value if (term := " ".join(v.split())))
    if declared in (int, "int") and isinstance(value, bool):
        raise ConfigError(f"[{section}].{key} must be an integer")
    if declared in (float, "float") and isinstance(value, int) and not isinstance(value, bool):
        # TOML tells 1 and 1.0 apart; the config file should not have to.
        return float(value)
    return value


def _validate(config: Config) -> None:
    """Check the enum-ish fields, so mistakes surface at startup, not mid-meeting."""
    t = config.transcription
    if t.device not in VALID_DEVICES:
        raise ConfigError(
            f"[transcription].device must be one of {VALID_DEVICES}, got {t.device!r}"
        )
    if t.compute_type not in VALID_COMPUTE_TYPES:
        raise ConfigError(
            f"[transcription].compute_type must be one of {VALID_COMPUTE_TYPES}, "
            f"got {t.compute_type!r}"
        )
    # Shape only. Whether "sv" is a language Whisper knows is a question for
    # faster-whisper, which is behind the `transcribe` extra and must not be
    # imported here -- `referat config` runs in the base install. The membership
    # check happens in transcribe.py against the loaded model, where the answer
    # is authoritative rather than a copy of somebody's list that goes stale.
    for code in t.languages:
        if not code or code != code.strip().lower() or not code.isalpha():
            raise ConfigError(
                f"[transcription].languages must be lowercase language codes, got {code!r}"
            )
    if len(set(t.languages)) != len(t.languages):
        raise ConfigError(f"[transcription].languages has a duplicate: {list(t.languages)}")
    level = config.app.log_level.upper()
    if level not in VALID_LOG_LEVELS:
        raise ConfigError(f"[app].log_level must be one of {VALID_LOG_LEVELS}, got {level!r}")
    if config.audio.mic_samplerate <= 0:
        raise ConfigError("[audio].mic_samplerate must be positive")
    for name, combo in (
        ("toggle_record", config.hotkeys.toggle_record),
        ("toggle_pause", config.hotkeys.toggle_pause),
    ):
        if not combo.strip():
            raise ConfigError(f"[hotkeys].{name} must not be empty")
    if config.hotkeys.toggle_record.lower() == config.hotkeys.toggle_pause.lower():
        raise ConfigError("[hotkeys].toggle_record and toggle_pause must differ")
    _validate_speakers(config.speakers)
    _validate_bleed(config.bleed)


def _validate_bleed(b: BleedConfig) -> None:
    """Check the suppression knobs. See :class:`BleedConfig` for what they mean."""
    for name, value in (
        ("contain", b.contain),
        ("back_contain", b.back_contain),
        ("cluster_time", b.cluster_time),
        ("cluster_text", b.cluster_text),
    ):
        if not 0.0 <= float(value) <= 1.0:
            raise ConfigError(f"[bleed].{name} must be between 0.0 and 1.0, got {value!r}")
    if b.window <= 0:
        raise ConfigError("[bleed].window must be positive")
    # A floor on the floor. This is the knob whose wrong value is most
    # destructive -- it is the only thing standing between a transcript and a rule
    # that deletes a line for matching one word -- so it is refused rather than
    # trusted, which no other numeric key here is.
    if b.min_tokens < 3:
        raise ConfigError(
            f"[bleed].min_tokens must be at least 3, got {b.min_tokens!r}: below that "
            "a containment rule matches lines that have nothing to do with each other"
        )


def _validate_speakers(s: SpeakersConfig) -> None:
    """Check the identification knobs. Imported lazily to keep this module light."""
    from referat.voices import name_complaint

    for name, value in (("match_threshold", s.match_threshold), ("match_margin", s.match_margin)):
        if not 0.0 <= float(value) <= 1.0:
            raise ConfigError(f"[speakers].{name} must be between 0.0 and 1.0, got {value!r}")
    if s.snippets_per_speaker < 1:
        raise ConfigError("[speakers].snippets_per_speaker must be at least 1")
    if s.snippet_seconds <= 0:
        raise ConfigError("[speakers].snippet_seconds must be positive")
    # An owner called REMOTE or SPEAKER_01 would collide with the labels the
    # pipeline generates, and the collision would be silent.
    if s.owner_name.strip() and (complaint := name_complaint(s.owner_name)):
        raise ConfigError(f"[speakers].owner_name {complaint}")


def ensure_meetings_dir(config: Config) -> Path:
    """Create the meetings folder and seed the parts of its scaffold that are missing.

    The scaffold is `templates/meetings/`: the folder's `CLAUDE.md`, the
    `/cleanup` slash command, the rule denying that pass access to `.voices/`, and
    the workspace setting that opens Markdown rendered. Seeded file by file and
    never overwritten, so a prompt refined in place survives — see
    :func:`referat.paths.seed_tree`.
    """
    meetings = config.paths.meetings_dir
    meetings.mkdir(parents=True, exist_ok=True)
    for created in paths.seed_tree(paths.MEETINGS_TEMPLATE_DIR, meetings):
        log.info("seeded %s", created)
    return meetings
