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
    # -- lists ------------------------------------------------------------

    def test_new_store_has_one_list(self) -> None:
        store = TodoStore(self.path)
        self.assertEqual([lst.name for lst in store.lists], ["General"])
        item = store.add("task")
        self.assertEqual(item.list_id, store.lists[0].id)

    def test_add_list_rejects_blank_and_duplicate_names(self) -> None:
        store = TodoStore(self.path)
        self.assertIsNone(store.add_list("   "))
        self.assertIsNone(store.add_list("general"))  # case-insensitive
        created = store.add_list("  Phy  ")
        self.assertIsNotNone(created)
        self.assertEqual(created.name, "Phy")
        self.assertEqual(len(store.lists), 2)

    def test_tasks_go_into_the_requested_list(self) -> None:
        store = TodoStore(self.path)
        phy = store.add_list("Phy")
        chy = store.add_list("Chy")
        store.add("vectors", phy.id)
        store.add("benzene", chy.id)
        self.assertEqual([i.text for i in store.items_for(phy.id)], ["vectors"])
        self.assertEqual([i.text for i in store.items_for(chy.id)], ["benzene"])
        self.assertEqual(len(store.items_for()), 2)
        self.assertEqual(store.counts(phy.id), (1, 0))

    def test_move_between_lists(self) -> None:
        store = TodoStore(self.path)
        phy = store.add_list("Phy")
        chy = store.add_list("Chy")
        item = store.add("task", phy.id)
        self.assertFalse(store.move(item.id, phy.id))  # already there
        self.assertFalse(store.move("missing", chy.id))
        self.assertFalse(store.move(item.id, "missing-list"))
        self.assertTrue(store.move(item.id, chy.id))
        self.assertEqual(store.get(item.id).list_id, chy.id)
        self.assertEqual([i.text for i in TodoStore(self.path).items_for(chy.id)], ["task"])

    def test_delete_list_takes_its_tasks_with_it(self) -> None:
        store = TodoStore(self.path)
        phy = store.add_list("Phy")
        chy = store.add_list("Chy")
        store.add("vectors", phy.id)
        store.add("benzene", chy.id)
        store.add("orbitals", chy.id)
        self.assertEqual(store.delete_list(phy.id), 1)
        self.assertEqual([lst.name for lst in store.lists], ["General", "Chy"])
        self.assertEqual([i.text for i in store.items], ["benzene", "orbitals"])
        # ...and the removal is on disk, not just in memory.
        reloaded = TodoStore(self.path)
        self.assertEqual([lst.name for lst in reloaded.lists], ["General", "Chy"])
        self.assertEqual(len(reloaded.items), 2)

    def test_delete_empty_list_reports_zero(self) -> None:
        store = TodoStore(self.path)
        empty = store.add_list("Empty")
        self.assertEqual(store.delete_list(empty.id), 0)
        self.assertEqual(len(store.lists), 1)

    def test_delete_list_refuses_unknown_and_last_list(self) -> None:
        store = TodoStore(self.path)
        only = store.lists[0]
        self.assertIsNone(store.delete_list("nope"))
        self.assertIsNone(store.delete_list(only.id))
        self.assertEqual([lst.id for lst in store.lists], [only.id])
        # The store still works afterwards.
        store.add("still here")
        self.assertEqual(len(TodoStore(self.path).items), 1)

    def test_old_single_list_file_migrates(self) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "items": [
                        {"id": "a", "text": "m4", "done": False},
                        {"id": "b", "text": "bode", "done": True},
                    ]
                }
            ),
            encoding="utf-8",
        )
        store = TodoStore(self.path)
        self.assertEqual([lst.name for lst in store.lists], ["General"])
        self.assertEqual([i.text for i in store.items], ["m4", "bode"])
        self.assertTrue(all(i.list_id == store.lists[0].id for i in store.items))

    def test_clear_completed_can_be_scoped_to_one_list(self) -> None:
        store = TodoStore(self.path)
        phy = store.add_list("Phy")
        chy = store.add_list("Chy")
        keep = store.add("keep", chy.id)
        gone = store.add("gone", phy.id)
        store.toggle(gone.id)
        self.assertEqual(store.clear_completed(phy.id), 1)
        self.assertEqual([i.id for i in store.items], [keep.id])


if __name__ == "__main__":
    unittest.main()
