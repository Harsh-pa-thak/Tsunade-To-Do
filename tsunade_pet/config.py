"""Configuration and on-disk locations.

Everything the user can tweak lives in ``~/.config/tsunade-pet/config.json`` so
the app itself stays read-only. Writes are atomic (write + rename) so a crash
can never leave a half-written file behind.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

APP_NAME = "tsunade-pet"

MIN_DISPLAY_HEIGHT = 150
MAX_DISPLAY_HEIGHT = 900


def data_dir() -> Path:
    """Where config, to-do list and the single-instance lock live."""
    override = os.environ.get("TSUN_PET_HOME")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    root = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return root / APP_NAME


def config_file() -> Path:
    return data_dir() / "config.json"


def todo_file() -> Path:
    return data_dir() / "todos.json"


def lock_file() -> Path:
    return data_dir() / "pet.lock"


def read_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", delete=False
    )
    try:
        with handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


@dataclass
class Config:
    """User settings, persisted between runs."""

    version: int = 1
    #: Top-left corner of the pet window; ``None`` means "bottom-right of the screen".
    position: dict | None = None
    #: Height of the sprite on screen, in logical pixels.
    display_height: int = 162
    always_on_top: bool = True
    #: How long she sits still before the first idle animation.
    static_delay_ms: int = 5000
    #: How often the idle animation repeats afterwards; 0 keeps her still.
    idle_interval_ms: int = 18000
    reactions: bool = True
    speech: bool = True
    visible: bool = True
    todo_geometry: dict | None = None
    extra: dict = field(default_factory=dict)

    # -- persistence ------------------------------------------------------

    @classmethod
    def load(cls) -> "Config":
        raw = read_json(config_file(), {})
        if not isinstance(raw, dict):
            raw = {}
        known = {key: raw[key] for key in raw if key in cls.__dataclass_fields__}
        config = cls(**known)
        config.display_height = config.clamp_height(config.display_height)
        config.static_delay_ms = max(0, int(config.static_delay_ms))
        config.idle_interval_ms = max(0, int(config.idle_interval_ms))
        if not isinstance(config.position, dict):
            config.position = None
        return config

    def save(self) -> None:
        write_json(config_file(), asdict(self))

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def clamp_height(height: float) -> int:
        return int(max(MIN_DISPLAY_HEIGHT, min(MAX_DISPLAY_HEIGHT, round(height))))
