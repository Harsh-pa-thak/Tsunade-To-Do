#!/usr/bin/env python3
"""End-to-end check of the pet, driven by synthetic input on the real desktop.

Runs the actual :class:`~tsunade_pet.pet.PetWindow` on the current display,
feeds it mouse/wheel events and inspects what happens: the 5 s static pose, the
single animation that follows, click reactions, drag, double-click to-do list
and the to-do list itself. Screenshots land in ``/tmp/tsunade-pet-smoke/``.

Usage:  python3 tools/smoke_test.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

WORKDIR = Path(tempfile.mkdtemp(prefix="tsunade-pet-smoke-"))
os.environ["TSUN_PET_HOME"] = str(WORKDIR)
# Same platform choice the real launcher makes, and it has to happen before Qt loads.
os.environ.setdefault("TSUN_PET_PLATFORM", "xcb")
SHOTS = Path("/tmp/tsunade-pet-smoke")
SHOTS.mkdir(parents=True, exist_ok=True)

from tsunade_pet.app import select_platform  # noqa: E402

select_platform()

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPixmap, QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tsunade_pet.assets import AssetLibrary  # noqa: E402
from tsunade_pet.config import Config, config_file  # noqa: E402
from tsunade_pet.pet import PetWindow  # noqa: E402
from tsunade_pet.todo import TodoStore, TodoWindow  # noqa: E402


class Results:
    def __init__(self) -> None:
        self.passed = 0
        self.failed: list[str] = []

    def check(self, condition: bool, message: str, detail: str = "") -> bool:
        if condition:
            self.passed += 1
            print(f"  pass  {message}")
        else:
            self.failed.append(message)
            print(f"  FAIL  {message}" + (f"  ({detail})" if detail else ""))
        return bool(condition)

    def report(self) -> int:
        print()
        if self.failed:
            print(f"{len(self.failed)} check(s) failed:")
            for message in self.failed:
                print(f"  - {message}")
        print(f"{self.passed} checks passed, {len(self.failed)} failed")
        return 1 if self.failed else 0


def wait_until(predicate, timeout_s: float, step_ms: int = 15) -> bool:
    """Pump the event loop until ``predicate`` holds or the timeout expires."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        QTest.qWait(step_ms)
    return bool(predicate())


def send_mouse_at(widget, kind, global_pos: QPoint, button=Qt.LeftButton, buttons=Qt.LeftButton) -> None:
    """Send a mouse event at an absolute screen position.

    Dragging moves the window, so the pointer position must be absolute -
    mapping a window-relative point after each move would feed the movement
    back into itself.
    """
    local = widget.mapFromGlobal(global_pos)
    event = QMouseEvent(kind, QPointF(local), QPointF(global_pos), button, buttons, Qt.NoModifier)
    QApplication.sendEvent(widget, event)


def send_mouse(widget, kind, pos: QPoint, button=Qt.LeftButton, buttons=Qt.LeftButton) -> None:
    send_mouse_at(widget, kind, widget.mapToGlobal(pos), button, buttons)


def click(widget, pos: QPoint) -> None:
    send_mouse(widget, QMouseEvent.Type.MouseButtonPress, pos)
    send_mouse(widget, QMouseEvent.Type.MouseButtonRelease, pos, buttons=Qt.NoButton)


def double_click(widget, pos: QPoint) -> None:
    send_mouse(widget, QMouseEvent.Type.MouseButtonPress, pos)
    send_mouse(widget, QMouseEvent.Type.MouseButtonRelease, pos, buttons=Qt.NoButton)
    send_mouse(widget, QMouseEvent.Type.MouseButtonDblClick, pos)
    send_mouse(widget, QMouseEvent.Type.MouseButtonRelease, pos, buttons=Qt.NoButton)


def pause_idle_cycle(pet) -> None:
    """Freeze the repeating idle animation so interaction checks stay deterministic."""
    pet.config.idle_interval_ms = 0
    pet._idle_timer.stop()


def screenshot(widget, name: str, background: str | None = None) -> Path:
    pixmap = widget.grab()
    if background:
        canvas = QPixmap(pixmap.size())
        canvas.fill(QColor(background))
        painter = QPainter(canvas)
        painter.drawPixmap(0, 0, pixmap)
        painter.end()
        pixmap = canvas
    path = SHOTS / name
    pixmap.save(str(path))
    return path


def main() -> int:
    results = Results()
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)

    config = Config.load()  # real defaults: 5 s static, 18 s idle repeat
    library = AssetLibrary()
    todo_store = TodoStore()
    pet = PetWindow(library, config)
    todo = TodoWindow(todo_store, library.icon())
    pet.todo_requested.connect(todo.present)

    started: list[str] = []
    finished: list[str] = []
    pet.player.started.connect(started.append)
    pet.player.finished.connect(finished.append)

    center = QPoint(pet.width() // 2, pet.height() // 2)
    scale = config.display_height / library.render_height

    print(f"config: static {config.static_delay_ms} ms, idle repeats every {config.idle_interval_ms} ms")
    print(f"pet window: {pet.width()}x{pet.height()} at {pet.pos().x()},{pet.pos().y()}  (platform {app.platformName()})")

    # ---------------------------------------------------------------- startup
    print("\nstartup pose")
    pet.start()
    results.check(pet.isVisible(), "the pet is on screen")
    results.check(not pet.player.is_playing, "she starts on the static pose")
    results.check(pet.displayed_pixmap is library.static, "the resting pixmap is the static frame")
    screenshot(pet, "01-static.png", "#1b1626")

    region = pet.mask()
    window_area = pet.width() * pet.height()
    mask_area = sum(r.width() * r.height() for r in region)
    results.check(region.rectCount() > 5, "transparent areas are masked out for the mouse", str(region.rectCount()))
    results.check(
        mask_area < window_area * 0.92,
        "the input mask is smaller than the window",
        f"{mask_area}/{window_area}",
    )

    print(f"\nwaiting {config.static_delay_ms} ms before the first animation")
    QTest.qWait(int(config.static_delay_ms * 0.6))
    results.check(
        not pet.player.is_playing,
        f"still static {int(config.static_delay_ms * 0.6)} ms in",
    )
    results.check(pet.displayed_pixmap is library.static, "and still showing the static frame")

    results.check(wait_until(lambda: started, 4.0), "the idle animation starts after the static pause")
    results.check(started[:1] == ["idle"], "the first animation is the idle one", str(started))
    QTest.qWait(400)
    screenshot(pet, "02-idle.png", "#1b1626")

    results.check(wait_until(lambda: finished, 6.0), "the idle animation finishes")
    results.check(not pet.player.is_playing, "and she returns to the static pose")
    results.check(pet.displayed_pixmap is library.static, "the displayed frame is the static one again")

    during_wait: list[str] = []
    pet.player.started.connect(during_wait.append)
    results.check(
        config.idle_interval_ms > 0,
        "the idle rhythm is on by default and well clear of the static pause",
        str(config.idle_interval_ms),
    )

    # The shipped interval is 18 s; shorten it here so the repeat can be checked
    # without a 20 s wait. The pet is on a real desktop, so the watch list keeps
    # the assertion true even if a human click lands mid-check.
    config.idle_interval_ms = 1500
    pet._idle_timer.start(1500)
    during_wait.clear()
    results.check(
        wait_until(lambda: "idle" in during_wait, 6.0),
        "the idle animation repeats on her own rhythm",
        str(during_wait),
    )
    results.check(
        wait_until(lambda: not pet.player.is_playing, 6.0),
        "and finishes again",
    )
    pet.player.started.disconnect(during_wait.append)
    pause_idle_cycle(pet)
    QTest.qWait(600)  # outrun any pending double-click window

    # ------------------------------------------------------------------ click
    print("\nclick reaction")
    started.clear()
    finished.clear()
    click(pet, center)
    results.check(
        wait_until(lambda: started, 2.0),
        "a single click triggers a reaction",
    )
    results.check(started[:1] and started[0] in library.reaction_names, "the reaction is one of her moves", str(started))
    QTest.qWait(300)
    screenshot(pet, "03-reaction.png", "#1b1626")
    results.check(pet.player.is_playing, "the reaction is still playing (it animates)")

    bubble = pet.speech
    results.check(bubble.isVisible(), "a speech bubble pops up with the reaction")
    body = bubble.rect().adjusted(0, 0, 0, -12)  # the tail hangs below the body
    results.check(
        body.contains(bubble.label.geometry()),
        "the text fits inside the bubble body",
        f"label {bubble.label.geometry().getRect()} in body {body.getRect()}",
    )
    long_line = "Practicing the Strength of a Hundred seal takes concentration."
    pet.say(long_line)
    QTest.qWait(80)
    body = bubble.rect().adjusted(0, 0, 0, -12)
    results.check(
        body.contains(bubble.label.geometry()) and bubble.width() <= 280,
        "a long line wraps inside the bubble",
        f"bubble {bubble.size().toTuple()} label {bubble.label.geometry().getRect()}",
    )
    screenshot(bubble, "09-speech-long.png", "#1b1626")
    results.check(
        bubble.y() + bubble.height() <= pet.y() + 20,
        "the bubble sits above her head",
        f"bubble bottom {bubble.y() + bubble.height()} vs pet top {pet.y()}",
    )
    results.check(
        abs(bubble.x() + bubble.width() // 2 - (pet.x() + pet.width() // 2)) <= 12,
        "the bubble is lined up with her",
        f"bubble centre {bubble.x() + bubble.width() // 2} vs pet centre {pet.x() + pet.width() // 2}",
    )
    screen_rect = app.primaryScreen().availableGeometry()
    results.check(
        screen_rect.contains(bubble.geometry()),
        "the bubble stays on screen",
        f"{bubble.geometry().getRect()} in {screen_rect.getRect()}",
    )
    screenshot(bubble, "08-speech.png", "#1b1626")
    results.check(wait_until(lambda: len(finished) >= 1, 4.0), "the reaction ends")
    results.check(not pet.player.is_playing, "playback stops")
    results.check(pet.displayed_pixmap is library.static, "and she settles back to static")
    results.check(
        wait_until(lambda: not bubble.isVisible(), 4.0),
        "the speech bubble disappears on its own",
    )

    pet._reaction_pool.clear()  # start a fresh shuffle, as a new run would
    pool = [pet._next_reaction() for _ in range(len(library.reaction_names))]
    results.check(
        len(set(pool)) == len(library.reaction_names),
        "the reaction pool cycles through every move without repeating",
        str(pool),
    )

    seen: list[str] = []
    for _ in range(len(library.reaction_names)):
        QTest.qWait(600)  # keep every press outside the double-click window
        started.clear()
        click(pet, center)
        if wait_until(lambda: started, 2.0):
            seen.append(pet.player.current)
        wait_until(lambda: not pet.player.is_playing, 4.0)
    results.check(
        bool(seen) and all(name in library.reaction_names for name in seen),
        "every click plays one of her reactions",
        str(seen),
    )
    results.check(
        all(a != b for a, b in zip(seen, seen[1:])),
        "consecutive clicks do not repeat the same move",
        str(seen),
    )
    QTest.qWait(700)

    # ------------------------------------------------------------------- drag
    print("\ndragging")
    started.clear()
    before = pet.pos()
    press_global = pet.mapToGlobal(center)
    delta = QPoint(-140, -160)
    send_mouse_at(pet, QMouseEvent.Type.MouseButtonPress, press_global)
    for step in range(1, 5):
        send_mouse_at(
            pet,
            QMouseEvent.Type.MouseMove,
            press_global + QPoint(delta.x() * step // 4, delta.y() * step // 4),
        )
    send_mouse_at(pet, QMouseEvent.Type.MouseButtonRelease, press_global + delta, buttons=Qt.NoButton)
    QTest.qWait(120)
    moved = pet.pos() - before
    landed = pet.pos()
    results.check(
        abs(moved.x() - delta.x()) <= 4 and abs(moved.y() - delta.y()) <= 4,
        "the window follows a drag",
        f"moved {moved.x()},{moved.y()} (wanted {delta.x()},{delta.y()})",
    )
    results.check(not started, "dragging does not fire a reaction", str(started))
    results.check(pet.displayed_pixmap is library.static, "she is still on the static pose")
    QTest.qWait(700)  # let the debounced config save land
    saved = json.loads(config_file().read_text(encoding="utf-8"))
    results.check(
        saved.get("position") == {"x": landed.x(), "y": landed.y()},
        "the new position is persisted",
        f"saved {saved.get('position')} vs landed {landed.x()},{landed.y()}",
    )
    screenshot(pet, "04-dragged.png", "#1b1626")

    # -------------------------------------------------------------- wheel zoom
    print("\nwheel resizing")
    height_before = config.display_height
    wheel = QWheelEvent(
        QPointF(center),
        QPointF(pet.mapToGlobal(center)),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
        Qt.MouseEventSource.MouseEventNotSynthesized,
    )
    QApplication.sendEvent(pet, wheel)
    QTest.qWait(200)
    results.check(
        config.display_height > height_before,
        "scrolling up makes her bigger",
        f"{height_before} -> {config.display_height}",
    )
    results.check(
        abs(pet.width() - round(library.cell.width() * config.display_height / library.render_height)) <= 1,
        "the window matches the new scale",
    )
    results.check(pet.mask().rectCount() > 5, "the input mask is rebuilt after resizing")
    screenshot(pet, "05-resized.png", "#1b1626")

    # ------------------------------------------------------------- double click
    print("\ndouble click opens the to-do list")
    started.clear()
    results.check(not todo.isVisible(), "the to-do window starts hidden")
    QTest.qWait(600)  # outrun any pending double-click window
    double_click(pet, center)
    QTest.qWait(700)
    results.check(todo.isVisible(), "a double click opens the to-do window")
    results.check(not started, "and does not also trigger a reaction", str(started))
    results.check(not pet._click_timer.isActive(), "no single-click action is left armed")
    todo.move(pet.pos() + QPoint(pet.width() + 20, -80))
    QTest.qWait(50)

    # ------------------------------------------------------------------- tasks
    print("\nto-do list")
    todo.input.setText("Feed the slugs")
    QTest.keyClick(todo.input, Qt.Key_Return)
    todo.input.setText("Water the bonsai")
    QTest.keyClick(todo.input, Qt.Key_Return)
    todo.input.setText("Practice the Strength of a Hundred seal")
    QTest.keyClick(todo.input, Qt.Key_Return)
    QTest.qWait(100)
    results.check(todo.list.count() == 3, "tasks appear in the list", str(todo.list.count()))
    results.check(todo.count.text() == "3 left", "the counter shows what is left", todo.count.text())
    results.check("0 of 3" in todo.count.toolTip(), "and carries the detail in its tooltip", todo.count.toolTip())
    results.check(
        todo.count.sizeHint().width() + todo.clear_button.sizeHint().width() + todo.close_button.sizeHint().width()
        <= todo.width() - 40,
        "the footer fits without eliding",
        f"{todo.count.sizeHint().width()}+{todo.clear_button.sizeHint().width()}+{todo.close_button.sizeHint().width()} vs {todo.width()}",
    )
    screenshot(todo, "06-todo-open.png")

    rows = [todo.list.itemWidget(todo.list.item(i)) for i in range(todo.list.count())]
    QTest.mouseClick(rows[0].check, Qt.LeftButton)
    QTest.qWait(100)
    refreshed = [todo.list.itemWidget(todo.list.item(i)) for i in range(todo.list.count())]
    results.check(refreshed[0].check.isChecked(), "clicking a box checks the task off")
    results.check(refreshed[0].label.font().strikeOut(), "finished tasks are struck through")
    results.check(todo.count.text() == "2 left", "the counter counts it", todo.count.text())
    results.check("1 of 3" in todo.count.toolTip(), "the tooltip tracks it too", todo.count.toolTip())
    results.check(
        json.loads(store_path(todo).read_text(encoding="utf-8"))["items"][0]["done"] is True,
        "the change is written to disk",
    )
    screenshot(todo, "07-todo-checked.png")

    QTest.mouseClick(refreshed[1].delete, Qt.LeftButton)
    QTest.qWait(100)
    texts = [todo.list.itemWidget(todo.list.item(i)).label.text() for i in range(todo.list.count())]
    results.check(texts == ["Feed the slugs", "Practice the Strength of a Hundred seal"], "the delete button removes a task", str(texts))

    todo.clear_button.click()
    QTest.qWait(100)
    texts = [todo.list.itemWidget(todo.list.item(i)).label.text() for i in range(todo.list.count())]
    results.check(texts == ["Practice the Strength of a Hundred seal"], "clear completed drops only finished tasks", str(texts))
    results.check(todo.hint.isHidden(), "the empty-state hint stays hidden while tasks exist")

    # external edits are picked up when the window is re-opened
    payload = json.loads(store_path(todo).read_text(encoding="utf-8"))
    payload["items"].append({"id": "outside", "text": "Added behind her back", "done": False})
    store_path(todo).write_text(json.dumps(payload), encoding="utf-8")
    todo.hide()
    QTest.qWait(50)
    todo.present()
    QTest.qWait(100)
    results.check(todo.list.count() == 2, "re-opening reloads the file from disk", str(todo.list.count()))

    # Empty state
    for _ in range(todo.list.count()):
        row = todo.list.itemWidget(todo.list.item(0))
        QTest.mouseClick(row.delete, Qt.LeftButton)
        QTest.qWait(60)
    results.check(todo.list.count() == 0 and todo.hint.isVisible(), "the empty state comes back")
    results.check(todo.count.text() == "No tasks yet", "the counter resets", todo.count.text())
    results.check(not todo.clear_button.isEnabled(), "clear is disabled with nothing done")

    leftovers = list(Path(WORKDIR).glob("todos.json.*"))
    results.check(not leftovers, "no temporary files were left behind", str(leftovers))

    # ------------------------------------------------------------------ close
    print("\nshutdown")
    pet.stop()
    pet.flush()
    todo.hide()
    pet.hide()
    QTest.qWait(80)
    results.check(not pet.player.is_playing, "stopping the pet halts playback")
    print(f"\nscreenshots: {SHOTS}")
    return results.report()


def store_path(todo: TodoWindow) -> Path:
    return Path(todo.store.path)


if __name__ == "__main__":
    raise SystemExit(main())
