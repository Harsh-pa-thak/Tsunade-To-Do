"""Window-level tests for the to-do list, run off-screen (no desktop needed).

The prompts are modal, so the window exposes them as overridable methods
(:meth:`TodoWindow._ask_text`, ``_confirm``, ``_warn``) and these tests answer
them directly instead of opening a dialog.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

# Must happen before PySide6 loads, and keeps the suite headless.
os.environ["QT_QPA_PLATFORM"] = "offscreen"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tsunade_pet.todo import DIALOG_STYLE, TodoStore, TodoWindow  # noqa: E402

APP = QApplication.instance() or QApplication([])


class ScriptedWindow(TodoWindow):
    """A to-do window that answers its prompts from attributes."""

    def __init__(self, store: TodoStore) -> None:
        self.answer_confirm = True
        self.answer_text: str | None = None
        self.questions: list[str] = []
        self.warnings: list[str] = []
        super().__init__(store)

    def _confirm(self, title: str, question: str) -> bool:
        self.questions.append(question)
        return self.answer_confirm

    def _ask_text(self, title: str, label: str) -> str | None:
        return self.answer_text

    def _warn(self, message: str) -> None:
        self.warnings.append(message)

    def select_list(self, list_id: str) -> None:
        for index in range(self.tabs.count()):
            if self.tabs.tabData(index) == list_id:
                self.tabs.setCurrentIndex(index)
                return
        raise AssertionError("list tab not found")


class TodoWindowTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "todos.json"
        self.store = TodoStore(self.path)
        self.phy = self.store.add_list("Phy")
        self.chy = self.store.add_list("Chy")
        self.window = ScriptedWindow(self.store)

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        QTest.qWait(10)
        self._tmp.cleanup()

    # -- tabs -------------------------------------------------------------

    def test_tabs_are_all_plus_one_per_list(self) -> None:
        names = [self.window.tabs.tabText(i) for i in range(self.window.tabs.count())]
        self.assertEqual(names, ["All", "General", "Phy", "Chy"])
        self.assertIsNone(self.window.current_list_id())  # All

    def test_rows_in_all_show_their_list_tag(self) -> None:
        self.store.add("vectors", self.phy.id)
        self.window.refresh()
        self.assertEqual(len(self.store.items), 1)
        row = self.window.list.itemWidget(self.window.list.item(0))
        self.assertIsNotNone(row.tag)
        self.assertEqual(row.tag.text(), "Phy")

    def test_add_list_from_the_prompt(self) -> None:
        self.window.answer_text = " Maths "
        self.window._new_list()
        self.assertEqual([lst.name for lst in self.store.lists][-1], "Maths")
        self.assertEqual(self.window.tabs.tabText(self.window.tabs.currentIndex()), "Maths")

    def test_duplicate_list_name_warns_instead_of_adding(self) -> None:
        self.window.answer_text = "phy"
        self.window._new_list()
        self.assertEqual(len(self.store.lists), 3)
        self.assertEqual(len(self.window.warnings), 1)
        self.assertIn("already a list called", self.window.warnings[0])
        self.assertIn("phy", self.window.warnings[0].lower())

    def test_cancelled_prompt_adds_nothing(self) -> None:
        self.window.answer_text = None
        self.window._new_list()
        self.assertEqual(len(self.store.lists), 3)

    # -- deleting lists ---------------------------------------------------

    def test_delete_list_button_only_on_a_real_list(self) -> None:
        self.assertFalse(self.window.delete_list_button.isEnabled())  # All
        self.window.select_list(self.phy.id)
        self.assertTrue(self.window.delete_list_button.isEnabled())

    def test_last_list_cannot_be_deleted(self) -> None:
        single = TodoStore(Path(self._tmp.name) / "single.json")
        window = ScriptedWindow(single)
        try:
            window.select_list(single.lists[0].id)
            self.assertFalse(window.delete_list_button.isEnabled())
            window._delete_list()  # a stray call must be harmless
            self.assertEqual(len(single.lists), 1)
        finally:
            window.close()
            window.deleteLater()

    def test_delete_list_button_removes_the_list_and_its_tasks(self) -> None:
        self.store.add("vectors", self.phy.id)
        self.store.add("benzene", self.chy.id)
        self.window.refresh()
        self.window.select_list(self.phy.id)

        QTest.mouseClick(self.window.delete_list_button, Qt.LeftButton)

        self.assertEqual([lst.name for lst in self.store.lists], ["General", "Chy"])
        self.assertEqual([i.text for i in self.store.items], ["benzene"])
        self.assertEqual(len(self.window.questions), 1)
        self.assertIn("Phy", self.window.questions[0])
        self.assertIn("1 task", self.window.questions[0])
        # Back on All, where the remaining task is visible.
        self.assertIsNone(self.window.current_list_id())
        self.assertEqual([self.window.tabs.tabText(i) for i in range(self.window.tabs.count())],
                         ["All", "General", "Chy"])
        self.assertFalse(TodoStore(self.path).list_by_id(self.phy.id))

    def test_cancelling_the_confirmation_keeps_everything(self) -> None:
        self.store.add("vectors", self.phy.id)
        self.window.refresh()
        self.window.select_list(self.phy.id)
        self.window.answer_confirm = False

        QTest.mouseClick(self.window.delete_list_button, Qt.LeftButton)

        self.assertEqual(len(self.store.lists), 3)
        self.assertEqual(len(self.store.items), 1)
        self.assertEqual(self.window.current_list_id(), self.phy.id)
        self.assertEqual(len(self.window.questions), 1)

    def test_confirmation_wording_for_an_empty_list(self) -> None:
        self.window.select_list(self.chy.id)
        self.window._delete_list()
        self.assertIn("empty list", self.window.questions[0])
        self.assertEqual([lst.name for lst in self.store.lists], ["General", "Phy"])

    # -- dialogs ----------------------------------------------------------

    def test_dialog_style_is_readable_on_the_dark_theme(self) -> None:
        self.assertIn("background: #2b1a24", DIALOG_STYLE)
        self.assertIn("QMessageBox QLabel", DIALOG_STYLE)
        self.assertIn("color: #f7efe2", DIALOG_STYLE)


if __name__ == "__main__":
    unittest.main()
