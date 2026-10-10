"""Frame-by-frame playback of the generated sprite sheets."""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QPixmap

from .assets import AssetLibrary


class AnimationPlayer(QObject):
    """Plays one animation at a time and emits its frames in order.

    ``finished`` fires after the last requested repetition, whether the
    animation loops on screen or not - the pet uses it to settle back into the
    resting pose.
    """

    frame_changed = Signal(QPixmap)
    started = Signal(str)
    finished = Signal(str)

    def __init__(self, library: AssetLibrary, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.library = library
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._advance)
        self._frames: list[QPixmap] = []
        self._index = 0
        self._repetitions_left = 0
        self._name: str | None = None

    # -- state ------------------------------------------------------------

    @property
    def current(self) -> str | None:
        """Name of the animation being played, or ``None`` when idle."""
        return self._name

    @property
    def is_playing(self) -> bool:
        return self._name is not None

    # -- controls ---------------------------------------------------------

    def play(self, name: str, repetitions: int = 1) -> None:
        """Start ``name`` from its first frame, replacing whatever was playing."""
        self._timer.stop()
        spec = self.library.spec(name)
        self._frames = self.library.frames(name)
        self._index = 0
        self._repetitions_left = max(1, repetitions)
        self._name = name
        self.started.emit(name)
        self.frame_changed.emit(self._frames[0])
        self._schedule()

    def stop(self) -> None:
        """Stop playback without reporting a finish (the caller owns the pose)."""
        self._timer.stop()
        self._name = None
        self._frames = []

    # -- internals --------------------------------------------------------

    def _schedule(self) -> None:
        self._timer.start(self.library.spec(self._name).frame_ms if self._name else 60)

    def _advance(self) -> None:
        if not self._name:
            return
        self._index += 1
        if self._index >= len(self._frames):
            self._repetitions_left -= 1
            if self._repetitions_left <= 0:
                name = self._name
                self.stop()
                self.finished.emit(name)
                return
            self._index = 0
        self.frame_changed.emit(self._frames[self._index])
        self._schedule()
