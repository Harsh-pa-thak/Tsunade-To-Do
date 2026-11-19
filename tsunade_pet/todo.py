"""The to-do list: a small JSON store plus the window that edits it.

:class:`TodoStore` is plain Python (no Qt) so it can be unit-tested headlessly;
:class:`TodoWindow` is the presentation layer on top of it.

Tasks live in named lists. The window shows one tab per list plus an *All* tab
that gathers every task, a button for creating lists, and a move control on
each task so it can be sent to another list.
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
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .config import todo_file, write_json

#: Name of the list an existing single-list file is migrated into, and of the
#: first list a brand-new file gets.
DEFAULT_LIST_NAME = "General"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _clean(text: object) -> str:
    return " ".join(str(text or "").split())


@dataclass
class TodoList:
    id: str
    name: str


@dataclass
class TodoItem:
    id: str
    text: str
    done: bool = False
    created: str = ""
    list_id: str = ""


class TodoStore:
    """A tiny, crash-safe task list backed by one JSON file."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else todo_file()
        self.items: list[TodoItem] = []
        self.lists: list[TodoList] = []
        #: Set when a damaged file had to be set aside on load.
        self.recovered_from: str | None = None
        self.load()

    # -- disk -------------------------------------------------------------

    def load(self) -> list[TodoItem]:
        self.items = []
        self.lists = []
        self.recovered_from = None
        if not self.path.exists():
            self._normalise()
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
            self._normalise()
            return self.items

        list_entries = raw.get("lists") if isinstance(raw, dict) else None
        seen_lists: set[str] = set()
        for entry in list_entries or []:
            if not isinstance(entry, dict):
                continue
            name = _clean(entry.get("name"))
            if not name:
                continue
            list_id = str(entry.get("id") or uuid.uuid4().hex)
            if list_id in seen_lists:
                list_id = uuid.uuid4().hex
            seen_lists.add(list_id)
            self.lists.append(TodoList(id=list_id, name=name))

        entries = raw.get("items") if isinstance(raw, dict) else None
        seen: set[str] = set()
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            text = _clean(entry.get("text"))
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
                    list_id=str(entry.get("list_id") or ""),
                )
            )
        # Older files have no ``lists`` key and no ``list_id`` on their tasks;
        # they end up in one default list, keeping their order intact.
        self._normalise()
        return self.items

    def save(self) -> None:
        write_json(
            self.path,
            {
                "version": 2,
                "updated": _now(),
                "lists": [{"id": lst.id, "name": lst.name} for lst in self.lists],
                "items": [
                    {
                        "id": i.id,
                        "text": i.text,
                        "done": i.done,
                        "created": i.created,
                        "list_id": i.list_id,
                    }
                    for i in self.items
                ],
            },
        )

    def _normalise(self) -> None:
        """Guarantee at least one list and that every task belongs to one."""
        if not self.lists:
            self.lists.append(TodoList(id=uuid.uuid4().hex, name=DEFAULT_LIST_NAME))
        valid = {lst.id for lst in self.lists}
        for item in self.items:
            if item.list_id not in valid:
                item.list_id = self.lists[0].id

    # -- queries ----------------------------------------------------------

    def get(self, item_id: str) -> TodoItem | None:
        return next((i for i in self.items if i.id == item_id), None)

    def list_by_id(self, list_id: str | None) -> TodoList | None:
        return next((lst for lst in self.lists if lst.id == list_id), None)

    def items_for(self, list_id: str | None = None) -> list[TodoItem]:
        """Tasks in one list, or every task when *list_id* is ``None``."""
        if list_id is None:
            return list(self.items)
        return [i for i in self.items if i.list_id == list_id]

    def counts(self, list_id: str | None = None) -> tuple[int, int]:
        """``(total, done)`` for one list, or for everything."""
        items = self.items_for(list_id)
        return len(items), sum(1 for i in items if i.done)

    @property
    def done_count(self) -> int:
        return sum(1 for i in self.items if i.done)

    @property
    def pending_count(self) -> int:
        return len(self.items) - self.done_count

    # -- mutations --------------------------------------------------------

    def add_list(self, name: str) -> TodoList | None:
        """Create a list; ``None`` when the name is blank or already taken."""
        name = _clean(name)
        if not name:
            return None
        if any(lst.name.casefold() == name.casefold() for lst in self.lists):
            return None
        created = TodoList(id=uuid.uuid4().hex, name=name)
        self.lists.append(created)
        self.save()
        return created

    def add(self, text: str, list_id: str | None = None) -> TodoItem | None:
        text = _clean(text)
        if not text:
            return None
        if not self.lists:
            self._normalise()
        target = self.list_by_id(list_id) or self.lists[0]
        item = TodoItem(
            id=uuid.uuid4().hex, text=text, done=False, created=_now(), list_id=target.id
        )
        self.items.append(item)
        self.save()
        return item

    def delete_list(self, list_id: str) -> int | None:
        """Remove a list and every task in it.

        Returns how many tasks went with it, or ``None`` when the list does not
        exist or is the last one (there always has to be somewhere to put a
        task).
        """
        target = self.list_by_id(list_id)
        if target is None or len(self.lists) <= 1:
            return None
        before = len(self.items)
        self.items = [i for i in self.items if i.list_id != target.id]
        self.lists = [lst for lst in self.lists if lst.id != target.id]
        removed = before - len(self.items)
        self.save()
        return removed

    def move(self, item_id: str, list_id: str) -> bool:
        """Send a task to another list."""
        item = self.get(item_id)
        if item is None or self.list_by_id(list_id) is None or item.list_id == list_id:
            return False
        item.list_id = list_id
        self.save()
        return True

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

    def clear_completed(self, list_id: str | None = None) -> int:
        """Drop finished tasks - from one list, or from all of them."""
        before = len(self.items)
        self.items = [
            i
            for i in self.items
            if not (i.done and (list_id is None or i.list_id == list_id))
        ]
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
QLabel#tag {
    color: #c8aebd; background: #3a2430; border-radius: 6px;
    padding: 0 6px; font-size: 11px;
}
QLineEdit {
    background: #3a2430;
    border: 1px solid #5b3a4a;
    border-radius: 8px;
    padding: 7px 10px;
    color: #fdf6ea;
    selection-background-color: #b8860b;
}
QLineEdit:focus { border: 1px solid #f0b429; }
QComboBox {
    background: #3a2430;
    border: 1px solid #5b3a4a;
    border-radius: 8px;
    padding: 6px 8px;
    color: #fdf6ea;
}
QComboBox:focus { border: 1px solid #f0b429; }
QComboBox QAbstractItemView {
    background: #2b1a24;
    color: #f7efe2;
    border: 1px solid #5b3a4a;
    selection-background-color: #5a2b38;
}
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
QPushButton#ghost:disabled {
    color: #6b5561; border-color: #3d2a35; background: transparent;
}
QPushButton#tab {
    background: transparent;
    color: #c8aebd;
    border: none;
    border-radius: 7px;
    padding: 4px 11px;
    font-weight: 500;
}
QPushButton#tab:hover { background: #3a2430; color: #ffd977; }
QPushButton#tab:checked { background: #f0b429; color: #2b1a24; font-weight: 600; }
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
QMenu {
    background: #2b1a24; color: #f7efe2;
    border: 1px solid #5b3a4a; border-radius: 8px; padding: 4px;
}
QMenu::item { padding: 5px 16px; border-radius: 6px; }
QMenu::item:selected { background: #5a2b38; color: #ffd977; }
QMenu::separator { height: 1px; background: #462b39; margin: 4px 8px; }
QProgressBar {
    background: #3a2430; border: none; border-radius: 4px;
    height: 6px; text-align: center;
}
QProgressBar::chunk { background: #f0b429; border-radius: 4px; }
QFrame#rule { background: #462b39; max-height: 1px; border: none; }
"""

#: Dialogs are their own top-level windows, so they do not inherit the card's
#: colours. Without this they would draw near-white text on the system's light
#: dialog background - which is exactly how the "new list" and "delete list"
#: prompts used to come out unreadable.
DIALOG_STYLE = STYLE + """
QDialog, QMessageBox, QInputDialog { background: #2b1a24; }
QDialog QLabel, QMessageBox QLabel, QInputDialog QLabel { color: #f7efe2; }
QMessageBox QLabel#qt_msgbox_label { font-size: 13px; }
QDialog QPushButton, QMessageBox QPushButton, QInputDialog QPushButton {
    min-width: 82px;
    padding: 7px 16px;
}
QDialog QPushButton:default, QMessageBox QPushButton:default {
    background: #f0b429; color: #2b1a24;
}
"""


class TodoRow(QWidget):
    """One task: checkbox, text, optional list tag, move and delete buttons."""

    toggled = Signal(str, bool)
    deleted = Signal(str)
    moved = Signal(str, str)

    def __init__(
        self,
        item: TodoItem,
        lists: list[TodoList] | tuple = (),
        show_list: bool = False,
        parent: QWidget | None = None,
    ) -> None:
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

        self.list_name = next((lst.name for lst in lists if lst.id == item.list_id), "")
        self.tag: QLabel | None = None
        # Only worth showing when there is more than one list to tell apart,
        # and kept to one compact line so it never stretches the row.
        if show_list and self.list_name and len(lists) > 1:
            self.tag = QLabel(self.list_name, self)
            self.tag.setObjectName("tag")
            self.tag.setAlignment(Qt.AlignCenter)
            self.tag.setFixedHeight(18)
            self.tag.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            self.tag.setToolTip(f"In the list {self.list_name}")

        self.move: QToolButton | None = None
        others = [lst for lst in lists if lst.id != item.list_id]
        if others:
            self.move = QToolButton(self)
            self.move.setText("\u21c4")
            self.move.setToolTip("Move this task to another list")
            self.move.setCursor(Qt.PointingHandCursor)
            menu = QMenu(self.move)
            for lst in others:
                action = menu.addAction(lst.name)
                action.triggered.connect(
                    lambda _checked=False, lid=lst.id: self.moved.emit(self.item_id, lid)
                )
            menu.addSeparator()
            current = next((lst for lst in lists if lst.id == item.list_id), None)
            if current is not None:
                now = menu.addAction(f"now in {current.name}")
                now.setEnabled(False)
            self.move.setMenu(menu)
            self.move.setPopupMode(QToolButton.InstantPopup)

        self.delete = QToolButton(self)
        self.delete.setText("\u2715")
        self.delete.setToolTip("Delete task")
        self.delete.setCursor(Qt.PointingHandCursor)

        layout.addWidget(self.check)
        layout.addWidget(self.label, 1)
        if self.tag is not None:
            layout.addWidget(self.tag, 0, Qt.AlignVCenter)
        if self.move is not None:
            layout.addWidget(self.move)
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
        self.setMinimumSize(380, 460)
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
        subtitle = QLabel("Check a box; \u21c4 moves a task to another list.", card)
        subtitle.setObjectName("subtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles, 1)
        root.addLayout(header)

        rule = QFrame(card)
        rule.setObjectName("rule")
        rule.setFrameShape(QFrame.HLine)
        root.addWidget(rule)

        # -- lists ---------------------------------------------------------
        listbar = QHBoxLayout()
        listbar.setSpacing(6)
        self.tabs = QTabBar(card)
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setElideMode(Qt.ElideRight)
        self.tabs.setFocusPolicy(Qt.NoFocus)
        self.tabs.setStyleSheet(
            """
            QTabBar { background: transparent; }
            QTabBar::tab {
                background: #3a2430; color: #c8aebd;
                border: none; border-radius: 7px;
                padding: 5px 11px; margin-right: 4px;
                font-weight: 500;
            }
            QTabBar::tab:selected { background: #f0b429; color: #2b1a24; font-weight: 600; }
            QTabBar::tab:hover:!selected { background: #4d3040; color: #ffd977; }
            QTabBar QToolButton { background: #3a2430; border-radius: 4px; padding: 0 3px; }
            QTabBar QToolButton:hover { background: #5a2b38; }
            """
        )
        self.new_list_button = QPushButton("+ List", card)
        self.new_list_button.setObjectName("ghost")
        self.new_list_button.setToolTip("Add another list")
        self.new_list_button.setCursor(Qt.PointingHandCursor)
        self.delete_list_button = QPushButton("\u2212 List", card)
        self.delete_list_button.setObjectName("ghost")
        self.delete_list_button.setToolTip("Delete the list you are looking at")
        self.delete_list_button.setCursor(Qt.PointingHandCursor)
        listbar.addWidget(self.tabs, 1)
        listbar.addWidget(self.new_list_button)
        listbar.addWidget(self.delete_list_button)
        root.addLayout(listbar)

        # -- entry ---------------------------------------------------------
        entry = QHBoxLayout()
        entry.setSpacing(8)
        self.input = QLineEdit(card)
        self.input.setPlaceholderText("What needs doing today?")
        self.input.setClearButtonEnabled(True)
        self.target_list = QComboBox(card)
        self.target_list.setToolTip("New tasks go into this list")
        self.target_list.setMinimumContentsLength(6)
        self.add_button = QPushButton("Add", card)
        self.add_button.setCursor(Qt.PointingHandCursor)
        entry.addWidget(self.input, 1)
        entry.addWidget(self.target_list)
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
        self.new_list_button.clicked.connect(self._new_list)
        self.delete_list_button.clicked.connect(self._delete_list)
        self.tabs.currentChanged.connect(lambda _index: self.refresh())

        self.refresh()

    # -- helpers ----------------------------------------------------------

    def current_list_id(self) -> str | None:
        """The list the visible tab stands for; ``None`` is the *All* tab."""
        index = self.tabs.currentIndex()
        if index < 0:
            return None
        return self.tabs.tabData(index) or None

    def _tabs_match_store(self) -> bool:
        """Whether the tab bar already shows exactly the store's lists."""
        if self.tabs.count() != len(self.store.lists) + 1:
            return False
        if self.tabs.tabData(0) != "" or self.tabs.tabText(0) != "All":
            return False
        return all(
            self.tabs.tabData(index + 1) == lst.id
            and self.tabs.tabText(index + 1) == lst.name
            for index, lst in enumerate(self.store.lists)
        )

    def _update_tab_tooltips(self) -> None:
        self.tabs.setTabToolTip(0, "Every task, whatever its list")
        for index in range(1, self.tabs.count()):
            total, done = self.store.counts(self.tabs.tabData(index))
            self.tabs.setTabToolTip(
                index,
                f"{total} task{'s' if total != 1 else ''}, {done} done"
                if total
                else "Empty list",
            )

    def _rebuild_tabs(self) -> None:
        wanted = self.current_list_id()
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        self.tabs.addTab("All")
        self.tabs.setTabData(0, "")
        selected = 0
        for lst in self.store.lists:
            index = self.tabs.addTab(lst.name)
            self.tabs.setTabData(index, lst.id)
            if lst.id == wanted:
                selected = index
        self.tabs.setCurrentIndex(selected)
        self.tabs.blockSignals(False)
        self._update_tab_tooltips()
        self._sync_target_list()

    def _sync_target_list(self) -> None:
        """Keep the "add into" picker in step with the lists and the tab."""
        current = self.current_list_id()
        previous = self.target_list.currentData()
        self.target_list.blockSignals(True)
        self.target_list.clear()
        for lst in self.store.lists:
            self.target_list.addItem(lst.name, lst.id)
        target = current if current is not None else previous
        index = self.target_list.findData(target) if target else -1
        self.target_list.setCurrentIndex(max(0, index))
        self.target_list.blockSignals(False)
        self.target_list.setVisible(current is None and self.target_list.count() > 1)

    def _on_toggled(self, item_id: str, done: bool) -> None:
        self.store.set_done(item_id, done)
        self.refresh()

    def _delete(self, item_id: str) -> None:
        self.store.remove(item_id)
        self.refresh()

    def _move(self, item_id: str, list_id: str) -> None:
        self.store.move(item_id, list_id)
        self.refresh()

    def _add(self) -> None:
        target = self.current_list_id() or self.target_list.currentData()
        if self.store.add(self.input.text(), target) is not None:
            self.input.clear()
            self.refresh()
            self.list.scrollToBottom()

    # -- dialogs ----------------------------------------------------------
    #
    # Each prompt is built, themed and run in one place and exposed as a small
    # overridable method, so the window can be tested without a modal loop.

    def _ask_text(self, title: str, label: str) -> str | None:
        """Ask for a single line of text; ``None`` when cancelled."""
        dialog = QInputDialog(self)
        dialog.setWindowTitle(title)
        dialog.setLabelText(label)
        dialog.setInputMode(QInputDialog.TextInput)
        dialog.setStyleSheet(DIALOG_STYLE)
        accepted = dialog.exec() == QDialog.Accepted
        text = dialog.textValue()
        dialog.deleteLater()
        return text if accepted else None

    def _warn(self, message: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Tsunade's To-Do list")
        box.setIcon(QMessageBox.Information)
        box.setText(message)
        box.setStandardButtons(QMessageBox.Ok)
        box.setStyleSheet(DIALOG_STYLE)
        box.exec()
        box.deleteLater()

    def _confirm(self, title: str, question: str) -> bool:
        """Ask a yes/no question, defaulting to *no*."""
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setIcon(QMessageBox.Warning)
        box.setText(question)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        box.setStyleSheet(DIALOG_STYLE)
        answer = box.exec()
        box.deleteLater()
        return answer == QMessageBox.Yes

    def _new_list(self) -> None:
        name = self._ask_text("New list", "Name this list:")
        if name is None:
            return
        created = self.store.add_list(name)
        if created is None:
            if name.strip():
                self._warn(f"There is already a list called \u201c{_clean(name)}\u201d.")
            return
        self.refresh()
        for index in range(self.tabs.count()):
            if self.tabs.tabData(index) == created.id:
                self.tabs.setCurrentIndex(index)
                break
        self.input.setFocus()

    def _delete_list(self) -> None:
        list_id = self.current_list_id()
        if list_id is None or len(self.store.lists) <= 1:
            return
        target = self.store.list_by_id(list_id)
        if target is None:
            return
        total, _done = self.store.counts(target.id)
        if total == 0:
            question = f"Delete the empty list \u201c{target.name}\u201d?"
        else:
            question = (
                f"Delete the list \u201c{target.name}\u201d and its {total} "
                f"task{'s' if total != 1 else ''}?\n\nThis cannot be undone."
            )
        if not self._confirm("Delete list", question):
            return
        self.store.delete_list(target.id)
        self.tabs.setCurrentIndex(0)
        self.refresh()

    def _clear_completed(self) -> None:
        if self.store.clear_completed(self.current_list_id()):
            self.refresh()

    # -- API --------------------------------------------------------------

    def refresh(self) -> None:
        """Rebuild the rows (and, when needed, the tabs) from the store."""
        if self._tabs_match_store():
            self._update_tab_tooltips()
            self._sync_target_list()
        else:
            self._rebuild_tabs()
        list_id = self.current_list_id()

        self.list.clear()
        for item in self.store.items_for(list_id):
            row = TodoRow(
                item, self.store.lists, show_list=list_id is None, parent=self.list
            )
            row.toggled.connect(self._on_toggled)
            row.deleted.connect(self._delete)
            row.moved.connect(self._move)
            entry = QListWidgetItem(self.list)
            entry.setData(Qt.UserRole, item.id)
            entry.setSizeHint(row.sizeHint())
            self.list.addItem(entry)
            self.list.setItemWidget(entry, row)

        total, done = self.store.counts(list_id)
        pending = total - done
        self.hint.setText(
            "Nothing here yet - add your first task above."
            if list_id is None
            else "Nothing in this list yet."
        )
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
            else f"{done} of {total} tasks done in this view."
        )
        self.progress.setToolTip(f"{done} of {total} tasks done")
        can_delete = list_id is not None and len(self.store.lists) > 1
        self.delete_list_button.setEnabled(can_delete)
        self.delete_list_button.setToolTip(
            "Delete this list and everything in it"
            if can_delete
            else (
                "Open a list to delete it"
                if list_id is None
                else "At least one list has to remain"
            )
        )
        self.clear_button.setEnabled(done > 0)
        self.clear_button.setToolTip(
            "Remove the finished tasks here" if done else "Nothing to clear yet"
        )

    def reload_from_disk(self) -> None:
        """Pick up edits made outside this window."""
        before = (
            [(lst.id, lst.name) for lst in self.store.lists],
            [(i.id, i.text, i.done, i.list_id) for i in self.store.items],
        )
        self.store.load()
        after = (
            [(lst.id, lst.name) for lst in self.store.lists],
            [(i.id, i.text, i.done, i.list_id) for i in self.store.items],
        )
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
