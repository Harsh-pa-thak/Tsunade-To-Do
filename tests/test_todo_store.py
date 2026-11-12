"""Unit tests for the to-do store (no Qt, no display needed)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tsunade_pet.todo import TodoStore  # noqa: E402


class TodoStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "todos.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- basics -----------------------------------------------------------

    def test_empty_when_file_missing(self) -> None:
        store = TodoStore(self.path)
        self.assertEqual(store.items, [])
        self.assertEqual((store.done_count, store.pending_count), (0, 0))

    def test_add_trims_and_ignores_blank(self) -> None:
        store = TodoStore(self.path)
        self.assertIsNone(store.add("   "))
        self.assertIsNone(store.add("\n\t"))
        item = store.add("  water the   plants  ")
        self.assertIsNotNone(item)
        self.assertEqual(item.text, "water the plants")
        self.assertFalse(item.done)
        self.assertEqual(store.pending_count, 1)

    def test_add_returns_stable_ids(self) -> None:
        store = TodoStore(self.path)
        first = store.add("one")
        second = store.add("two")
        self.assertNotEqual(first.id, second.id)
        self.assertIs(store.get(first.id), first)

    # -- mutations --------------------------------------------------------

    def test_toggle_and_set_done(self) -> None:
        store = TodoStore(self.path)
        item = store.add("task")
        self.assertTrue(store.toggle(item.id))
        self.assertTrue(store.get(item.id).done)
        self.assertEqual(store.done_count, 1)
        # A no-op transition reports no change (so the UI can skip a redraw).
        self.assertFalse(store.set_done(item.id, True))
        self.assertFalse(store.toggle("missing-id"))
        self.assertTrue(store.set_done(item.id, False))
        self.assertEqual(store.pending_count, 1)

    def test_remove(self) -> None:
        store = TodoStore(self.path)
        item = store.add("task")
        self.assertTrue(store.remove(item.id))
        self.assertFalse(store.remove(item.id))
        self.assertEqual(store.items, [])

    def test_clear_completed_only(self) -> None:
        store = TodoStore(self.path)
        keep = store.add("keep me")
        done = store.add("finish me")
        store.toggle(done.id)
        self.assertEqual(store.clear_completed(), 1)
        self.assertEqual([i.id for i in store.items], [keep.id])
        self.assertEqual(store.clear_completed(), 0)

    def test_order_is_preserved(self) -> None:
        store = TodoStore(self.path)
        texts = [store.add(f"task {i}").text for i in range(4)]
        self.assertEqual([i.text for i in TodoStore(self.path).items], texts)

    # -- persistence ------------------------------------------------------

    def test_round_trip(self) -> None:
        store = TodoStore(self.path)
        store.add("first")
        second = store.add("second")
        store.toggle(second.id)

        reloaded = TodoStore(self.path)
        self.assertEqual([i.text for i in reloaded.items], ["first", "second"])
        self.assertEqual(reloaded.done_count, 1)
        self.assertEqual(reloaded.items[1].id, second.id)
        self.assertTrue(reloaded.items[1].created)

    def test_write_is_atomic_and_leaves_valid_json(self) -> None:
        store = TodoStore(self.path)
        store.add("task")
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], 2)
        self.assertEqual(payload["items"][0]["text"], "task")
        self.assertEqual([p.name for p in self.path.parent.glob("todos.json.*")], [])

    def test_corrupt_file_is_set_aside_not_crashed_on(self) -> None:
        self.path.write_text("{ not json at all", encoding="utf-8")
        store = TodoStore(self.path)
        self.assertEqual(store.items, [])
        self.assertIsNotNone(store.recovered_from)
        self.assertTrue(Path(store.recovered_from).exists())
        # The store is usable again straight away.
        store.add("fresh start")
        self.assertEqual(len(TodoStore(self.path).items), 1)

    def test_hand_edited_file_is_sanitised(self) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "items": [
                        {"id": "dup", "text": "one"},
                        {"id": "dup", "text": "two"},
                        {"id": "x", "text": "   "},
                        {"text": "no id"},
                        "not a dict",
                        {"id": "y", "text": "three", "done": True},
                    ]
                }
            ),
            encoding="utf-8",
        )
        store = TodoStore(self.path)
        self.assertEqual([i.text for i in store.items], ["one", "two", "no id", "three"])
        self.assertEqual(len({i.id for i in store.items}), len(store.items))
        self.assertTrue(store.items[-1].done)


if __name__ == "__main__":
    unittest.main()
