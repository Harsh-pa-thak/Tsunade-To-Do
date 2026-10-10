"""The pet itself: a frameless, always-on-top sprite you can drag around.

Interaction map
---------------
``drag``          move her anywhere on the desktop
``click``         she reacts (jiggle / turn around / hop / head shake) and talks
``double click``  the to-do list opens
``right click``   options, including quit
``wheel``         resize her

Idle sequence: she holds the resting pose for ``static_delay_ms`` (5 s by
default), plays the idle animation exactly once, settles back into the resting
pose, then repeats that after ``idle_interval_ms``.
"""

from __future__ import annotations

import random

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
    QRegion,
)
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QVBoxLayout, QWidget

from .animations import AnimationPlayer
from .assets import AssetLibrary
from .config import Config

DRAG_THRESHOLD = 6  # px of movement before a press counts as a drag
BUBBLE_MS = 1900  # how long a speech bubble stays up
BUBBLE_PAD_X = 14  # bubble body padding, px
BUBBLE_PAD_Y = 9
BUBBLE_TAIL = 12  # height of the little pointer at the bottom of the bubble
MIN_VISIBLE = 80  # px of the sprite that must stay on screen
# The window shape (and therefore which clicks reach her) is built from the
# artwork's alpha channel. The threshold has to be low: anything visible but
# left outside the shape gets clipped by the window manager, which eats her
# feathered edge, the ground shadow and the faded hem - and how much of them is
# lost depends on how big she is drawn. Only fully transparent pixels may fall
# outside, so that clicks land on the desktop behind her.
MASK_BLOCK = 4  # px grid used to turn her silhouette into a window shape
MASK_THRESHOLD = 8  # alpha above which a block counts as "her"


def silhouette_region(pixmap, block: int = MASK_BLOCK, threshold: int = MASK_THRESHOLD) -> QRegion:
    """A coarse window shape covering everything visible in ``pixmap``.

    Qt 6 dropped the bitmap based ``QRegion`` constructor, and a per-pixel
    region would be far too heavy, so the alpha channel is folded into a grid
    of ``block``-sized rectangles instead.
    """
    alpha = pixmap.toImage().convertToFormat(QImage.Format_Alpha8)
    width, height = alpha.width(), alpha.height()
    if width <= 0 or height <= 0:
        return QRegion()
    bits = alpha.constBits()
    region = QRegion()
    for top in range(0, height, block):
        bottom = min(top + block, height)
        for left in range(0, width, block):
            right = min(left + block, width)
            hit = False
            for y in range(top, bottom):
                row = y * width
                for x in range(left, right):
                    if bits[row + x] >= threshold:
                        hit = True
                        break
                if hit:
                    break
            if hit:
                region += QRect(left, top, block, block)
    return region


class SpeechBubble(QWidget):
    """A small always-on-top balloon; it never takes mouse or keyboard input."""

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.Tool
            | Qt.WindowStaysOnTopHint
            | Qt.WindowTransparentForInput
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        self.label.setMaximumWidth(230)
        self.label.setStyleSheet("color: #ffeee0; font-size: 12px; background: transparent;")
        layout = QVBoxLayout(self)
        # Side padding for the rounded body, extra room at the bottom for the tail.
        layout.setContentsMargins(BUBBLE_PAD_X, BUBBLE_PAD_Y, BUBBLE_PAD_X, BUBBLE_TAIL + 5)
        layout.addWidget(self.label)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def say(self, text: str, pet_rect: QRect) -> None:
        self.label.setText(text)
        self.adjustSize()
        x = pet_rect.center().x() - self.width() // 2
        y = pet_rect.top() - self.height() + 10
        screen = QGuiApplication.screenAt(pet_rect.center()) or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            x = min(max(x, area.left() + 4), area.right() - self.width() - 4)
            y = max(y, area.top() + 4)
        self.move(x, y)
        self.show()
        self.raise_()
        self._timer.start(BUBBLE_MS)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        body = QRect(0, 0, self.width() - 1, self.height() - BUBBLE_TAIL)
        path = QPainterPath()
        path.addRoundedRect(body, 11, 11)
        tail = QPainterPath()
        cx = body.center().x()
        tail.moveTo(cx - 8, body.bottom())
        tail.lineTo(cx, body.bottom() + BUBBLE_TAIL - 1)
        tail.lineTo(cx + 8, body.bottom())
        tail.closeSubpath()
        path = path.united(tail)
        painter.fillPath(path, QColor(46, 27, 38, 240))
        painter.setPen(QColor(240, 180, 41, 220))
        painter.drawPath(path)
        painter.end()


class PetWindow(QWidget):
    """Frameless sprite window with drag, click and wheel handling."""

    todo_requested = Signal()
    quit_requested = Signal()

    def __init__(self, library: AssetLibrary, config: Config) -> None:
        super().__init__(None)
        self.library = library
        self.config = config
        self.player = AnimationPlayer(library, self)
        self.player.frame_changed.connect(self._on_frame)
        self.player.finished.connect(self._on_animation_finished)

        self.setWindowTitle("Tsunade Pet")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.OpenHandCursor)

        self._pixmap = library.static
        self._scale = 1.0
        self._press_global: QPoint | None = None
        self._drag_offset = QPoint()
        self._dragging = False
        self._swallow_release = False
        self._flag_change = False
        self.speech = SpeechBubble()
        self._reaction_pool: list[str] = []
        self._misses = 0  # consecutive ignored clicks, for a cheeky nudge

        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self.react)

        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._idle_beat)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self.config.save)

        self._apply_window_flags()
        self._apply_display_height(config.display_height)
        self.restore_position()

    # ------------------------------------------------------------------ state

    @property
    def displayed_pixmap(self):
        """What is painted right now: the resting pose or an animation frame."""
        return self._pixmap

    # ------------------------------------------------------------------ setup

    def _apply_window_flags(self) -> None:
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self.config.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        was_visible = self.isVisible()
        # Re-applying window flags hides and reshows the widget; that is not the
        # user hiding her, so visibility tracking pauses for the swap.
        self._flag_change = True
        try:
            self.setWindowFlags(flags)
            if was_visible:
                self.show()
        finally:
            self._flag_change = False

    def _apply_display_height(self, height: float) -> None:
        """Resize the window to ``height`` px tall, keeping the cell ratio."""
        self.config.display_height = Config.clamp_height(height)
        self._scale = self.config.display_height / max(1, self.library.render_height)
        size = QSize(
            max(40, round(self.library.cell.width() * self._scale)),
            max(60, round(self.library.cell.height() * self._scale)),
        )
        self.setFixedSize(size)
        self._update_mask()
        self.update()

    def _update_mask(self) -> None:
        """Let clicks fall through the transparent parts of the sprite.

        The shape is the union of every animation frame, generated with the
        artwork, so it never clips her mid-jump and does not flicker while she
        animates.
        """
        try:
            scaled = self.library.mask.scaled(
                self.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation
            )
            self.setMask(silhouette_region(scaled))
        except Exception:  # pragma: no cover - masking is a nicety, never fatal
            self.setMask(QRegion())

    # -------------------------------------------------------------- geometry

    def default_position(self) -> QPoint:
        screen = QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen else QRect(0, 0, 1280, 800)
        return QPoint(area.right() - self.width() - 60, area.bottom() - self.height() - 30)

    def clamp_position(self, pos: QPoint) -> QPoint:
        screen = QGuiApplication.screenAt(pos + QPoint(self.width() // 2, self.height() - 40))
        if screen is None:
            screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        if screen is None:
            return pos
        area = screen.availableGeometry()
        x = min(max(pos.x(), area.left() - self.width() + MIN_VISIBLE), area.right() - MIN_VISIBLE)
        y = min(max(pos.y(), area.top() - self.height() + MIN_VISIBLE), area.bottom() - 40)
        return QPoint(x, y)

    def restore_position(self) -> None:
        raw = self.config.position or {}
        try:
            pos = QPoint(int(raw.get("x")), int(raw.get("y")))
        except (TypeError, ValueError):
            pos = self.default_position()
        self.move(self.clamp_position(pos))

    def move_to(self, pos: QPoint) -> None:
        self.move(self.clamp_position(pos))

    def _save_position(self) -> None:
        self.config.position = {"x": self.x(), "y": self.y()}
        self._queue_save()

    def _queue_save(self) -> None:
        self._save_timer.start(500)

    def flush(self) -> None:
        """Persist pending changes immediately (used on quit)."""
        if self._save_timer.isActive():
            self._save_timer.stop()
        self.config.save()

    # ------------------------------------------------------------ animations

    def play_animation(self, name: str) -> None:
        self.player.play(name, repetitions=1)

    def _on_frame(self, pixmap) -> None:
        self._pixmap = pixmap
        self.update()

    def _idle_beat(self) -> None:
        """One beat of her own rhythm: breathe, unless something else is playing."""
        if self.config.idle_interval_ms <= 0 or not self.isVisible():
            return
        if self.player.is_playing:
            # A reaction is on screen; skip this beat rather than interrupt it.
            self._idle_timer.start(self.config.idle_interval_ms)
            return
        self.play_animation("idle")

    def _on_animation_finished(self, name: str) -> None:
        self.player.stop()
        self._pixmap = self.library.static
        self.update()
        # Every animation hands her back to the static pose. The idle rhythm is
        # her own: only a beat that actually played schedules the next one, so
        # poking her never pushes the breathing animation further away.
        if name == "idle" and self.config.idle_interval_ms > 0 and self.isVisible():
            self._idle_timer.start(self.config.idle_interval_ms)

    def start(self, show_window: bool = True) -> None:
        """Show her (unless she was hidden last time) and start the pose cycle."""
        first_run = self.config.position is None
        if show_window:
            self.show()
            if first_run:
                self._save_position()
                QTimer.singleShot(
                    1200, lambda: self.say("Hi! Double-click me for your to-do list.")
                )
        if show_window or self.config.visible:
            self._idle_timer.start(max(0, self.config.static_delay_ms))

    def stop(self) -> None:
        self._idle_timer.stop()
        self._click_timer.stop()
        self.player.stop()
        self.speech.hide()

    # --------------------------------------------------------------- reactions

    IDLE_LINES = [
        "Hmph. Something you need?",
        "Don't poke me... or do.",
        "I've got a to-do list, you know.",
        "You called?",
        "Five seconds of peace, that's all I ask.",
        "Need a hand? Double-click me.",
        "Watch it, kid.",
        "Nice try.",
        "I'm not your babysitter.",
        "Still here, huh?",
    ]
    BUSY_LINES = [
        "One thing at a time!",
        "Hold on, I'm busy being cute.",
        "Excuse you.",
    ]

    def _next_reaction(self) -> str:
        if not self._reaction_pool:
            self._reaction_pool = list(self.library.reaction_names)
            random.shuffle(self._reaction_pool)
        return self._reaction_pool.pop()

    def say(self, text: str | None = None) -> None:
        if not self.config.speech:
            return
        self.speech.say(text or random.choice(self.IDLE_LINES), self.frameGeometry())

    def react(self) -> None:
        """One click: a movement reaction plus a line of dialogue."""
        if not self.config.reactions:
            self._misses += 1
            if self._misses == 3:
                self.say("Right-click me if you want the menu.")
                self._misses = 0
            return
        self._misses = 0
        line = random.choice(self.BUSY_LINES) if self.player.is_playing else None
        self.play_animation(self._next_reaction())
        self.say(line)

    # ------------------------------------------------------------------ events

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        self._press_global = event.globalPosition().toPoint()
        self._drag_offset = self._press_global - self.frameGeometry().topLeft()
        self._dragging = False
        self.setCursor(Qt.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press_global is None:
            return
        global_pos = event.globalPosition().toPoint()
        if not self._dragging:
            if (global_pos - self._press_global).manhattanLength() < DRAG_THRESHOLD:
                return
            self._dragging = True
            self.speech.hide()
        self.move_to(global_pos - self._drag_offset)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return
        self._press_global = None
        self.setCursor(Qt.OpenHandCursor)
        if self._dragging:
            self._dragging = False
            self._save_position()
            return
        if self._swallow_release:
            # Second release of a double click: the to-do window already opened.
            self._swallow_release = False
            return
        self._click_timer.start(QApplication.doubleClickInterval() + 40)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return
        self._click_timer.stop()
        self._swallow_release = True
        self._press_global = None
        self.speech.hide()
        self.todo_requested.emit()

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if not delta:
            return
        ground = QPoint(self.x() + self.width() // 2, self.y() + self.height())
        self._apply_display_height(self.config.display_height * (1.08 if delta > 0 else 1 / 1.08))
        self.move_to(QPoint(ground.x() - self.width() // 2, ground.y() - self.height()))
        self._save_position()
        self.say(f"{self.config.display_height} px tall")
        event.accept()

    def hideEvent(self, event) -> None:  # noqa: N802
        self.speech.hide()
        if self._flag_change:
            super().hideEvent(event)
            return
        if self.config.visible:
            self.config.visible = False
            self._queue_save()
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        if self._flag_change:
            super().showEvent(event)
            return
        if not self.config.visible:
            self.config.visible = True
            self._queue_save()
        # Coming back from a tray toggle should also restart her rhythm.
        if self.config.idle_interval_ms > 0 and not self._idle_timer.isActive():
            self._idle_timer.start(self.config.static_delay_ms)
        super().showEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        menu.addAction("Open to-do list…", self.todo_requested.emit)
        menu.addAction("Do something cute", self.react)
        menu.addSeparator()

        top = menu.addAction("Always on top")
        top.setCheckable(True)
        top.setChecked(self.config.always_on_top)
        top.toggled.connect(self._set_always_on_top)

        talking = menu.addAction("Speech bubbles")
        talking.setCheckable(True)
        talking.setChecked(self.config.speech)
        talking.toggled.connect(self._set_speech)

        reacting = menu.addAction("React when clicked")
        reacting.setCheckable(True)
        reacting.setChecked(self.config.reactions)
        reacting.toggled.connect(self._set_reactions)

        animating = menu.addAction("Idle animations")
        animating.setCheckable(True)
        animating.setChecked(self.config.idle_interval_ms > 0)
        animating.toggled.connect(self._set_idle_animations)

        size_menu = menu.addMenu("Size")
        for label, factor in (("Small (75%)", 0.75), ("Medium (100%)", 1.0), ("Large (125%)", 1.25), ("Huge (150%)", 1.5)):
            action = size_menu.addAction(label)
            action.triggered.connect(lambda _=False, f=factor: self._set_size_factor(f))
        menu.addAction("Reset position", self._reset_position)
        menu.addSeparator()
        menu.addAction("Hide (use the tray icon to bring her back)", self.hide)
        menu.addAction("Quit", self.quit_requested.emit)
        menu.exec(event.globalPos())

    # -- context menu handlers -------------------------------------------

    def _set_always_on_top(self, enabled: bool) -> None:
        self.config.always_on_top = enabled
        self._apply_window_flags()
        self.move_to(self.pos())
        self._queue_save()

    def _set_speech(self, enabled: bool) -> None:
        self.config.speech = enabled
        if not enabled:
            self.speech.hide()
        self._queue_save()

    def _set_reactions(self, enabled: bool) -> None:
        self.config.reactions = enabled
        self._queue_save()

    def _set_idle_animations(self, enabled: bool) -> None:
        self.config.idle_interval_ms = 18000 if enabled else 0
        self._queue_save()

    def _set_size_factor(self, factor: float) -> None:
        ground = QPoint(self.x() + self.width() // 2, self.y() + self.height())
        self._apply_display_height(self.library.render_height * factor)
        self.move_to(QPoint(ground.x() - self.width() // 2, ground.y() - self.height()))
        self._save_position()

    def _reset_position(self) -> None:
        self.move_to(self.default_position())
        self._save_position()

    # -- painting ---------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawPixmap(self.rect(), self._pixmap)
        painter.end()
