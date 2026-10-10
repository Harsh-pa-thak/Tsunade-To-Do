"""Entry point: platform selection, single-instance guard and wiring.

On Linux both Wayland and XWayland are usually available. A native Wayland
client is not allowed to place its own windows, which would make dragging the
pet a no-op, so when ``DISPLAY`` is set the app runs on XWayland (``xcb``) by
default. Override with ``TSUN_PET_PLATFORM=wayland`` or any Qt platform name.
"""

from __future__ import annotations

import os
import sys


def select_platform() -> str | None:
    """Choose the Qt platform plugin *before* Qt is imported."""
    if not sys.platform.startswith("linux"):
        return None
    forced = os.environ.get("TSUN_PET_PLATFORM")
    if forced:
        os.environ["QT_QPA_PLATFORM"] = forced
        return forced
    if os.environ.get("QT_QPA_PLATFORM"):
        return os.environ["QT_QPA_PLATFORM"]
    if os.environ.get("WAYLAND_DISPLAY") and os.environ.get("DISPLAY"):
        os.environ["QT_QPA_PLATFORM"] = "xcb"
        return "xcb"
    return None


PLATFORM = select_platform()

from PySide6.QtCore import QLockFile, QRect  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
)

from . import __version__  # noqa: E402
from .assets import AssetError, AssetLibrary  # noqa: E402
from .config import Config, data_dir, lock_file  # noqa: E402
from .pet import PetWindow  # noqa: E402
from .todo import TodoStore, TodoWindow  # noqa: E402


def _restore_todo_geometry(window: TodoWindow, geometry: dict | None) -> None:
    if not isinstance(geometry, dict):
        return
    try:
        rect = QRect(int(geometry["x"]), int(geometry["y"]), int(geometry["w"]), int(geometry["h"]))
    except (KeyError, TypeError, ValueError):
        return
    if not (320 <= rect.width() <= 2000 and 300 <= rect.height() <= 2000):
        return
    # Only restore it if that spot still exists (monitors come and go).
    screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.screenAt(rect.topLeft())
    if screen is None:
        return
    area = screen.availableGeometry()
    rect.moveLeft(min(max(rect.left(), area.left()), area.right() - 100))
    rect.moveTop(min(max(rect.top(), area.top()), area.bottom() - 60))
    window.setGeometry(rect)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName("Tsunade Pet")
    app.setApplicationDisplayName("Tsunade Pet")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("tsunade-pet")
    app.setQuitOnLastWindowClosed(False)

    data_dir().mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(lock_file()))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        print("Tsunade is already on your desktop - look for her, or use the tray icon.")
        return 1

    config = Config.load()
    try:
        library = AssetLibrary()
    except AssetError as exc:
        print(f"error: {exc}")
        QMessageBox.critical(None, "Tsunade Pet", str(exc))
        return 2

    pet = PetWindow(library, config)
    store = TodoStore()
    todo = TodoWindow(store, library.icon())
    _restore_todo_geometry(todo, config.todo_geometry)
    if store.recovered_from:
        print(f"note: the task file was unreadable, a copy was kept at {store.recovered_from}")

    pet.todo_requested.connect(todo.present)

    def remember_todo_geometry() -> None:
        rect = todo.geometry()
        config.todo_geometry = {"x": rect.x(), "y": rect.y(), "w": rect.width(), "h": rect.height()}
        config.save()

    todo.geometry_changed.connect(remember_todo_geometry)

    def show_pet() -> None:
        pet.show()
        pet.raise_()
        pet.move_to(pet.pos())

    def toggle_pet() -> None:
        if pet.isVisible():
            pet.hide()
        else:
            show_pet()

    shutting_down = False

    def shutdown() -> None:
        nonlocal shutting_down
        if shutting_down:
            return
        shutting_down = True
        remember_todo_geometry()
        pet.stop()
        pet.flush()
        if tray is not None:
            tray.hide()
        app.quit()

    pet.quit_requested.connect(shutdown)

    tray: QSystemTrayIcon | None = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = QSystemTrayIcon(library.icon(), app)
        menu = QMenu()
        menu.addAction("Show Tsunade", show_pet)
        menu.addAction("Hide her for a while", pet.hide)
        menu.addAction("Open to-do list…", todo.present)
        menu.addSeparator()
        menu.addAction("Quit", shutdown)
        tray.setContextMenu(menu)
        tray.setToolTip("Tsunade Pet - double-click her to open your to-do list")
        tray.activated.connect(
            lambda reason: toggle_pet()
            if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick)
            else None
        )
        tray.show()

    app.aboutToQuit.connect(lambda: lock.unlock())

    # She remembers whether she was hidden when the app last closed. Without a
    # tray icon there would be no way back, so in that case always show her.
    pet.start(show_window=config.visible or tray is None)
    print(
        f"Tsunade Pet {__version__} running"
        f" (Qt platform: {app.platformName()}, settings: {data_dir()})"
    )
    if tray is None:
        print("note: no system tray found - use the right-click menu to quit.")
    code = app.exec()
    lock.unlock()
    return code
