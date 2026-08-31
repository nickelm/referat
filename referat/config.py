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
    language: str = "en"
    diarization: bool = True
    diarization_model: str = "pyannote/speaker-diarization-community-1"


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
class AppConfig:
    log_level: str = "INFO"


@dataclass(frozen=True)
class Config:
    hotkeys: HotkeysConfig = field(default_factory=HotkeysConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    speakers: SpeakersConfig = field(default_factory=SpeakersConfig)
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
