"""The to-do list: a small JSON store plus the window that edits it.

:class:`TodoStore` is plain Python (no Qt) so it can be unit-tested headlessly;
:class:`TodoWindow` is the presentation layer on top of it.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .config import todo_file, write_json


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class TodoItem:
    id: str
    text: str
    done: bool = False
    created: str = ""


class TodoStore:
    """A tiny, crash-safe task list backed by one JSON file."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else todo_file()
        self.items: list[TodoItem] = []
        #: Set when a damaged file had to be set aside on load.
        self.recovered_from: str | None = None
        self.load()

    # -- disk -------------------------------------------------------------

    def load(self) -> list[TodoItem]:
        self.items = []
        self.recovered_from = None
        if not self.path.exists():
            return self.items
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            backup = self.path.with_name(self.path.name + ".corrupt")
            try:
                os.replace(self.path, backup)
                self.recovered_from = str(backup)
            except OSError:
                pass
            return self.items

        entries = raw.get("items") if isinstance(raw, dict) else None
        seen: set[str] = set()
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            text = str(entry.get("text", "")).strip()
            if not text:
                continue
            item_id = str(entry.get("id") or uuid.uuid4().hex)
            if item_id in seen:
                item_id = uuid.uuid4().hex
            seen.add(item_id)
            self.items.append(
                TodoItem(
                    id=item_id,
                    text=text,
                    done=bool(entry.get("done", False)),
                    created=str(entry.get("created") or _now()),
                )
            )
        return self.items

    def save(self) -> None:
        write_json(
            self.path,
            {
                "version": 1,
                "updated": _now(),
                "items": [
                    {"id": i.id, "text": i.text, "done": i.done, "created": i.created}
                    for i in self.items
                ],
            },
        )

    # -- queries ----------------------------------------------------------

    def get(self, item_id: str) -> TodoItem | None:
        return next((i for i in self.items if i.id == item_id), None)

    @property
    def done_count(self) -> int:
        return sum(1 for i in self.items if i.done)

    @property
    def pending_count(self) -> int:
        return len(self.items) - self.done_count

    # -- mutations --------------------------------------------------------

    def add(self, text: str) -> TodoItem | None:
        text = " ".join(text.split())
        if not text:
            return None
        item = TodoItem(id=uuid.uuid4().hex, text=text, done=False, created=_now())
        self.items.append(item)
        self.save()
        return item

    def set_done(self, item_id: str, done: bool) -> bool:
        item = self.get(item_id)
        if item is None or item.done == done:
            return False
        item.done = done
        self.save()
        return True

    def toggle(self, item_id: str) -> bool:
        item = self.get(item_id)
        if item is None:
            return False
        return self.set_done(item_id, not item.done)

    def remove(self, item_id: str) -> bool:
        before = len(self.items)
        self.items = [i for i in self.items if i.id != item_id]
        if len(self.items) == before:
            return False
        self.save()
        return True

    def clear_completed(self) -> int:
        before = len(self.items)
        self.items = [i for i in self.items if not i.done]
        removed = before - len(self.items)
        if removed:
            self.save()
        return removed


# ---------------------------------------------------------------------------
# presentation
# ---------------------------------------------------------------------------

STYLE = """
QWidget#card {
    background: #2b1a24;
    border-radius: 14px;
}
QLabel { color: #f7efe2; }
QLabel#title { font-size: 16px; font-weight: 600; color: #ffd977; }
QLabel#subtitle { color: #c8aebd; }
QLabel#hint { color: #a98f9f; font-style: italic; }
QLabel#count { color: #e7d5b0; }
QLineEdit {
    background: #3a2430;
    border: 1px solid #5b3a4a;
    border-radius: 8px;
    padding: 7px 10px;
    color: #fdf6ea;
    selection-background-color: #b8860b;
}
QLineEdit:focus { border: 1px solid #f0b429; }
QPushButton {
    background: #f0b429;
    color: #2b1a24;
    border: none;
    border-radius: 8px;
    padding: 7px 14px;
    font-weight: 600;
}
QPushButton:hover { background: #ffc94d; }
QPushButton:pressed { background: #d99f1f; }
QPushButton:disabled { background: #4d3a44; color: #8d7683; }
QPushButton#ghost {
    background: transparent;
    color: #d9c3b0;
    border: 1px solid #5b3a4a;
    font-weight: 500;
}
QPushButton#ghost:hover { border-color: #f0b429; color: #ffd977; }
QListWidget {
    background: transparent;
    border: none;
    outline: none;
}
QListWidget::item { border-radius: 8px; margin: 2px 0; }
QListWidget::item:selected { background: #402b38; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
QScrollBar::handle:vertical { background: #553544; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid #8a6d3b; border-radius: 4px; background: #241620;
}
QCheckBox::indicator:hover { border-color: #f0b429; }
QCheckBox::indicator:checked { background: #f0b429; border-color: #f0b429; }
QToolButton {
    border: none; border-radius: 6px; color: #b08a9c;
    font-size: 14px; padding: 2px 6px; background: transparent;
}
QToolButton:hover { background: #5a2b38; color: #ffb3b3; }
QProgressBar {
    background: #3a2430; border: none; border-radius: 4px;
    height: 6px; text-align: center;
}
QProgressBar::chunk { background: #f0b429; border-radius: 4px; }
QFrame#rule { background: #462b39; max-height: 1px; border: none; }
"""


class TodoRow(QWidget):
    """One task: checkbox, text and a delete button."""

    toggled = Signal(str, bool)
    deleted = Signal(str)

    def __init__(self, item: TodoItem, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.item_id = item.id
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 4, 3)
        layout.setSpacing(9)

        self.check = QCheckBox(self)
        self.check.setChecked(item.done)
        self.check.setCursor(Qt.PointingHandCursor)
        self.check.setToolTip("Check the task off" if not item.done else "Mark as not done")

        self.label = QLabel(item.text, self)
        self.label.setWordWrap(True)
        self.label.setToolTip(item.text)
        self.label.setMinimumWidth(0)
        font = self.label.font()
        font.setStrikeOut(item.done)
        self.label.setFont(font)
        self.label.setStyleSheet("color: #a08e99;" if item.done else "")

        self.delete = QToolButton(self)
        self.delete.setText("\u2715")
        self.delete.setToolTip("Delete task")
        self.delete.setCursor(Qt.PointingHandCursor)

        layout.addWidget(self.check)
        layout.addWidget(self.label, 1)
        layout.addWidget(self.delete)

        # Connect only after the initial state is in place, so building a row
        # never reports a change back to the store.
        self.check.toggled.connect(lambda value: self.toggled.emit(self.item_id, value))
        self.delete.clicked.connect(lambda: self.deleted.emit(self.item_id))


class TaskListWidget(QListWidget):
    """The task list; ``Delete`` removes whatever is selected."""

    delete_requested = Signal(str)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            item = self.currentItem()
            if item is not None:
                self.delete_requested.emit(item.data(Qt.UserRole))
                return
        super().keyPressEvent(event)


class TodoWindow(QWidget):
    """The window the pet opens on a double click."""

    geometry_changed = Signal()

    def __init__(self, store: TodoStore, icon: QIcon | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Tsunade's To-Do list")
        if icon is not None:
            self.setWindowIcon(icon)
        self.setMinimumSize(360, 420)
        self.setStyleSheet(STYLE)

        self._geometry_timer = QTimer(self)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.timeout.connect(self.geometry_changed.emit)

        card = QWidget(self)
        card.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        root = QVBoxLayout(card)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        if icon is not None and not icon.isNull():
            badge = QLabel(card)
            badge.setPixmap(icon.pixmap(40, 40))
            header.addWidget(badge)
        titles = QVBoxLayout()
        titles.setSpacing(1)
        title = QLabel("Tsunade's To-Do list", card)
        title.setObjectName("title")
        subtitle = QLabel("Click a box to check a task off.", card)
        subtitle.setObjectName("subtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles, 1)
        root.addLayout(header)

        rule = QFrame(card)
        rule.setObjectName("rule")
        rule.setFrameShape(QFrame.HLine)
        root.addWidget(rule)

        entry = QHBoxLayout()
        entry.setSpacing(8)
        self.input = QLineEdit(card)
        self.input.setPlaceholderText("What needs doing today?")
        self.input.setClearButtonEnabled(True)
        self.add_button = QPushButton("Add", card)
        self.add_button.setCursor(Qt.PointingHandCursor)
        entry.addWidget(self.input, 1)
        entry.addWidget(self.add_button)
        root.addLayout(entry)

        self.hint = QLabel("Nothing here yet - add your first task above.", card)
        self.hint.setObjectName("hint")
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)

        self.list = TaskListWidget(card)
        self.list.setSelectionMode(QListWidget.SingleSelection)
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        root.addWidget(self.list, 1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.count = QLabel(card)
        self.count.setObjectName("count")
        self.clear_button = QPushButton("Clear done", card)
        self.clear_button.setObjectName("ghost")
        self.clear_button.setCursor(Qt.PointingHandCursor)
        self.close_button = QPushButton("Close", card)
        self.close_button.setObjectName("ghost")
        self.close_button.setCursor(Qt.PointingHandCursor)
        footer.addWidget(self.count)
        footer.addStretch(1)
        footer.addWidget(self.clear_button)
        footer.addWidget(self.close_button)
        root.addLayout(footer)

        self.progress = QProgressBar(card)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        root.addWidget(self.progress)

        self.input.returnPressed.connect(self._add)
        self.add_button.clicked.connect(self._add)
        self.clear_button.clicked.connect(self._clear_completed)
        self.close_button.clicked.connect(self.hide)
        self.list.delete_requested.connect(self._delete)

        self.refresh()

    # -- helpers ----------------------------------------------------------

    def _on_toggled(self, item_id: str, done: bool) -> None:
        self.store.set_done(item_id, done)
        self.refresh()

    def _delete(self, item_id: str) -> None:
        self.store.remove(item_id)
        self.refresh()

    def _add(self) -> None:
        if self.store.add(self.input.text()) is not None:
            self.input.clear()
            self.refresh()
            self.list.scrollToBottom()

    def _clear_completed(self) -> None:
        if self.store.clear_completed():
            self.refresh()

    # -- API --------------------------------------------------------------

    def refresh(self) -> None:
        """Rebuild the rows from the store."""
        self.list.clear()
        for item in self.store.items:
            row = TodoRow(item, self.list)
            row.toggled.connect(self._on_toggled)
            row.deleted.connect(self._delete)
            entry = QListWidgetItem(self.list)
            entry.setData(Qt.UserRole, item.id)
            entry.setSizeHint(row.sizeHint())
            self.list.addItem(entry)
            self.list.setItemWidget(entry, row)

        total = len(self.store.items)
        done = self.store.done_count
        pending = total - done
        self.hint.setVisible(total == 0)
        self.list.setVisible(total > 0)
        self.progress.setVisible(total > 0)
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        # Short enough to never elide next to the buttons - the bar and the
        # tooltip carry the detail.
        if total == 0:
            summary = "No tasks yet"
        elif pending == 0:
            summary = "All done \U0001f389"
        else:
            summary = f"{pending} left"
        self.count.setText(summary)
        self.count.setToolTip(
            "Add your first task to get going."
            if total == 0
            else f"{done} of {total} tasks done."
        )
        self.progress.setToolTip(f"{done} of {total} tasks done")
        self.clear_button.setEnabled(done > 0)
        self.clear_button.setToolTip(
            "Remove the finished tasks" if done else "Nothing to clear yet"
        )

    def reload_from_disk(self) -> None:
        """Pick up edits made outside this window."""
        before = [(i.id, i.text, i.done) for i in self.store.items]
        self.store.load()
        after = [(i.id, i.text, i.done) for i in self.store.items]
        if before != after:
            self.refresh()

    def present(self) -> None:
        self.reload_from_disk()
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    # -- events -----------------------------------------------------------

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if event.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        self._geometry_timer.start(600)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._geometry_timer.start(600)

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self.geometry_changed.emit()
