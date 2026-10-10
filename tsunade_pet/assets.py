"""Loading of the generated artwork.

``assets/sheets.json`` (written by ``tools/build_assets.py``) describes one
horizontal sprite sheet per animation plus the resting pose. Sheets are sliced
into frame pixmaps lazily, so a run only pays for the animations it plays.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon, QPixmap


class AssetError(RuntimeError):
    """Raised when the generated artwork is missing or does not add up."""


@dataclass(frozen=True)
class AnimationSpec:
    name: str
    label: str
    sheet: str
    frames: int
    frame_ms: int
    loop: bool
    reactions: bool


def default_asset_dir() -> Path:
    override = os.environ.get("TSUN_PET_ASSETS")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parent.parent / "assets"


class AssetLibrary:
    """Sprite sheets, the resting pose and the tray icon."""

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory) if directory else default_asset_dir()
        meta_path = self.directory / "sheets.json"
        if not meta_path.exists():
            raise AssetError(
                f"{meta_path} is missing - run 'python3 tools/build_assets.py' first."
            )
        try:
            self.meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise AssetError(f"cannot read {meta_path}: {exc}") from exc

        cell = self.meta.get("cell") or {}
        self.cell = QSize(int(cell.get("w", 0)), int(cell.get("h", 0)))
        if self.cell.width() <= 0 or self.cell.height() <= 0:
            raise AssetError(f"{meta_path} has no usable cell size")

        self.render_height = int(self.meta.get("render_height") or self.cell.height())
        self.reaction_names: list[str] = list(self.meta.get("reactions") or [])
        self.animations: dict[str, AnimationSpec] = {}
        for name, spec in (self.meta.get("animations") or {}).items():
            self.animations[name] = AnimationSpec(
                name=name,
                label=str(spec.get("label", name)),
                sheet=str(spec.get("sheet", f"sheet_{name}.png")),
                frames=int(spec.get("frames", 0)),
                frame_ms=max(10, int(spec.get("frame_ms", 60))),
                loop=bool(spec.get("loop", False)),
                reactions=bool(spec.get("reactions", False)),
            )
        if not self.animations:
            raise AssetError(f"{meta_path} describes no animations")

        self._pixmaps: dict[str, QPixmap] = {}
        self._frames: dict[str, list[QPixmap]] = {}
        #: How many sliced animations stay in memory. A playing animation holds
        #: its own reference, so evicting here never interrupts playback.
        self.max_cached_animations = 2

    # -- primitives -------------------------------------------------------

    def pixmap(self, relative: str) -> QPixmap:
        cached = self._pixmaps.get(relative)
        if cached is not None:
            return cached
        path = self.directory / relative
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            raise AssetError(f"cannot load artwork: {path}")
        self._pixmaps[relative] = pixmap
        return pixmap

    @property
    def static(self) -> QPixmap:
        """The resting pose, with its shadow, exactly one cell big."""
        return self.pixmap(str(self.meta.get("static", "tsunade_sprite.png")))

    @property
    def mask(self) -> QPixmap:
        """Silhouette of every frame, used as the window shape.

        Falls back to the resting pose for asset packs built before the mask
        was part of the output.
        """
        name = self.meta.get("mask")
        if name:
            try:
                return self.pixmap(str(name))
            except AssetError:
                pass
        return self.static

    def icon(self) -> QIcon:
        name = str(self.meta.get("icon", "tsunade_icon.png"))
        try:
            return QIcon(str(self.directory / name))
        except Exception:  # pragma: no cover - QIcon never raises in practice
            return QIcon()

    # -- animations -------------------------------------------------------

    def spec(self, name: str) -> AnimationSpec:
        try:
            return self.animations[name]
        except KeyError:
            raise AssetError(f"unknown animation {name!r}") from None

    def frames(self, name: str) -> list[QPixmap]:
        """Every frame of ``name``, sliced from its sheet on first use."""
        cached = self._frames.pop(name, None)
        if cached is not None:
            self._frames[name] = cached  # refresh its place in the cache order
            return cached
        spec = self.spec(name)
        sheet = self.pixmap(spec.sheet)
        expected = self.cell.width() * spec.frames
        if sheet.width() != expected or sheet.height() != self.cell.height():
            raise AssetError(
                f"{spec.sheet} is {sheet.width()}x{sheet.height()}, expected "
                f"{expected}x{self.cell.height()} for {spec.frames} frames - "
                "re-run 'python3 tools/build_assets.py'."
            )
        frames = [
            sheet.copy(i * self.cell.width(), 0, self.cell.width(), self.cell.height())
            for i in range(spec.frames)
        ]
        # Only the sliced frames are needed from here on; the full sheet is a
        # large pixmap and would otherwise stay resident forever.
        self._pixmaps.pop(spec.sheet, None)
        self._frames[name] = frames
        while len(self._frames) > self.max_cached_animations:
            oldest = next(iter(self._frames))
            if oldest == name:
                break
            self._frames.pop(oldest, None)
        return frames
