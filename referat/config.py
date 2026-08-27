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


@dataclass(frozen=True)
class TranscriptionConfig:
    model: str = "large-v3"
    cpu_fallback_model: str = "medium"
    device: str = "auto"
    compute_type: str = "auto"
    language: str = "en"
    diarization: bool = True


@dataclass(frozen=True)
class AppConfig:
    log_level: str = "INFO"


@dataclass(frozen=True)
class Config:
    hotkeys: HotkeysConfig = field(default_factory=HotkeysConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    transcription: TranscriptionConfig = field(default_factory=TranscriptionConfig)
    app: AppConfig = field(default_factory=AppConfig)
    source: Path | None = None
    """The file this config was read from, or None for pure defaults."""

    def hf_token(self) -> str | None:
        """The Hugging Face token, or None when the token file is missing or empty.

        Diarization needs it; everything else works without. Callers degrade to
        undifferentiated REMOTE labels rather than failing.
        """
        try:
            token = self.paths.hf_token_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return token or None


_SECTIONS: dict[str, type] = {
    "hotkeys": HotkeysConfig,
    "audio": AudioConfig,
    "paths": PathsConfig,
    "transcription": TranscriptionConfig,
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
    if declared in (Path, "Path"):
        if not isinstance(value, str):
            raise ConfigError(f"[{section}].{key} must be a path string")
        return Path(value).expanduser()
    if declared in (int, "int") and isinstance(value, bool):
        raise ConfigError(f"[{section}].{key} must be an integer")
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


def ensure_meetings_dir(config: Config) -> Path:
    """Create the meetings folder and seed its CLAUDE.md if absent."""
    meetings = config.paths.meetings_dir
    meetings.mkdir(parents=True, exist_ok=True)
    guide = meetings / "CLAUDE.md"
    if not guide.exists() and paths.MEETINGS_CLAUDE_TEMPLATE.exists():
        shutil.copyfile(paths.MEETINGS_CLAUDE_TEMPLATE, guide)
        log.info("seeded %s", guide)
    return meetings
