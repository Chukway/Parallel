"""Failed reward picks: what is saved, how the scanner reports unread cards, and the review tab."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

ROWS = [
    {"display_name": "Braton Prime Stock", "url_name": "braton_prime_stock", "price": 12, "ducats": 45,
     "slot_index": 0, "save_for_ducats": False, "vaulted": True, "trend": {"direction": "up", "arrow": "↑"},
     "owned": {"set_name": "Braton Prime Set", "have": 2, "total": 4}},
    {"display_name": "Lex Prime Barrel", "url_name": "lex_prime_barrel", "price": 3, "ducats": 15,
     "slot_index": 1, "save_for_ducats": True, "ducat_bulk_value": 2},
]


class HistoryFailureTests(unittest.TestCase):
    def history(self, directory):
        from reward_history import RewardHistory
        return RewardHistory(Path(directory) / "h.json")

    def test_record_keeps_prices_advice_and_set_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = self.history(tmp)
            history.record("s1", ROWS)
            first, second = self.history(tmp).screens[0]["rewards"]
            self.assertEqual((first["price"], first["ducats"], first["vaulted"]), (12, 45, True))
            self.assertEqual((first["set_have"], first["set_total"]), (2, 4))
            self.assertEqual(first["trend"]["arrow"], "↑")
            self.assertTrue(second["save_for_ducats"])

    def test_a_screen_where_nothing_was_read_can_still_be_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = self.history(tmp)
            self.assertEqual(history.mark_failed("blank"), 0)                       # unknown, not created
            number = history.mark_failed("blank", unread=[{"slot": 1, "text": "Cedo Pr", "candidates": [
                {"display_name": "Cedo Prime Barrel", "url_name": "cedo_prime_barrel"}]}], image="f.jpg", create=True)
            again = self.history(tmp).failures()
            self.assertEqual([s["failure_no"] for s in again], [number])
            self.assertEqual(again[0]["unread"][0]["candidates"][0]["url_name"], "cedo_prime_barrel")
            self.assertEqual(again[0]["image"], "f.jpg")

    def test_resolved_failures_leave_the_waiting_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            history = self.history(tmp)
            for name in ("a", "b", "c"):
                history.record(name, ROWS)
                history.mark_failed(name)
            history.mark_added("a", "braton_prime_stock")
            history.mark_dismissed("b")
            self.assertEqual([s["id"] for s in history.failures()], ["c"])
            self.assertEqual([s["id"] for s in history.failures(unresolved_only=False)], ["a", "b", "c"])

    def test_trimming_never_drops_a_failure_that_still_needs_a_decision(self):
        import reward_history
        with tempfile.TemporaryDirectory() as tmp:
            history = self.history(tmp)
            history.record("old-failure", ROWS)
            history.mark_failed("old-failure")
            for index in range(reward_history.MAX_SCREENS + 10):
                history.record(f"s{index}", ROWS)
            ids = [s["id"] for s in history.screens]
            self.assertIn("old-failure", ids)
            self.assertLessEqual(len(ids), reward_history.MAX_SCREENS)
            self.assertIn("old-failure", [s["id"] for s in self.history(tmp).screens])

    def test_trimming_removes_the_screenshot_of_a_dropped_screen(self):
        import reward_history
        with tempfile.TemporaryDirectory() as tmp:
            history = self.history(tmp)
            history.images_dir.mkdir()
            (history.images_dir / "gone.jpg").write_bytes(b"x")
            history.record("first", ROWS)
            history.mark_failed("first", image="gone.jpg")
            history.mark_dismissed("first")
            for index in range(reward_history.MAX_SCREENS + 1):
                history.record(f"s{index}", ROWS)
            self.assertFalse((history.images_dir / "gone.jpg").exists())


class ScannerFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="failure-scan-")
        cls.environment = patch.dict(os.environ, {"TTB_DATA_DIR": cls.directory.name, "QT_QPA_PLATFORM": "offscreen"})
        cls.environment.start()
        import reward_scanner
        cls.mod = reward_scanner

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()
        cls.directory.cleanup()

    def scanner(self, reads, matcher):
        scanner = self.mod.RewardScanner.__new__(self.mod.RewardScanner)
        scanner.unresolved_by_slot = {}
        scanner._ambiguous_note = None
        scanner._slot_ocr_cache = {}
        scanner._selected_name_cache = {}
        scanner.matcher = matcher
        scanner.items_cache = SimpleNamespace(url_name=lambda name: name.lower().replace(" ", "_"))
        scanner._card_rects = lambda frame: [(0, 0, 100, 100), (110, 0, 100, 100)]
        scanner._ocr = Mock()
        scanner._ocr.read_regions.side_effect = lambda prepared, regions, confidence_threshold=0: reads.pop(0)
        scanner._ocr.read_batch.side_effect = lambda images, batch_size=1: [[] for _ in images]
        return scanner

    def run_scan(self, scanner):
        frame = np.zeros((300, 400, 3), np.uint8)
        with patch.object(self.mod, "_scaled_int", lambda value, axis, frame: int(value)), \
             patch.object(self.mod, "preprocess_label_with_regions", lambda crop: (crop, [(0, 0, 1, 1)])), \
             patch.object(self.mod, "preprocess_label_raw", lambda crop: crop):
            return scanner.scan(use_fallback=True, frame=frame, detect_selected=False)

    @staticmethod
    def words(text):
        return [SimpleNamespace(raw_text=text)]

    def test_forma_cards_skip_the_slow_second_read_and_are_not_failures(self):
        ambiguous = SimpleNamespace(
            display_name="Cedo Prime Barrel", url_name="cedo_prime_barrel", match_score=90, raw_ocr="",
            alternatives=[("Cedo Prime Stock", 90), ("Cedo Prime Receiver", 89)],
        )
        scanner = self.scanner([self.words("2 X Forma Blueprint"), self.words("Cedo Pr")], Mock(match=Mock(return_value=ambiguous)))
        with patch("builtins.print"):
            result = self.run_scan(scanner)
        self.assertEqual(result.items, [])
        calls = scanner._ocr.read_batch.call_args_list
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0].args[0]), 1)                      # only the unread card is retried
        self.assertNotIn(0, scanner.unresolved_by_slot)                 # Forma is not a failure
        unread = scanner.unresolved_by_slot[1]
        self.assertEqual(unread["text"], "Cedo Pr")
        self.assertEqual({c["url_name"] for c in unread["candidates"]},
                         {"cedo_prime_barrel", "cedo_prime_stock", "cedo_prime_receiver"})

    def test_a_forma_card_is_settled_and_not_read_again_on_the_next_poll(self):
        scanner = self.scanner([self.words("Forma Blueprint"), self.words("Forma Blueprint")],
                               Mock(match=Mock(return_value=None)))
        with patch("builtins.print"):
            self.run_scan(scanner)
            scanner._ocr.read_regions.side_effect = AssertionError("a settled card must come from the cache")
            self.run_scan(scanner)
        scanner._ocr.read_batch.assert_not_called()

    def test_a_card_that_later_reads_clearly_leaves_the_unread_list(self):
        scanner = self.scanner([self.words("???")], Mock(match=Mock(return_value=None)))
        scanner._note_unresolved(0, self.words("Cedo Pr"))
        self.assertIn(0, scanner.unresolved_by_slot)
        scanner.unresolved_by_slot.pop(0)
        self.assertEqual(scanner.unresolved_by_slot, {})


class FailureTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="failure-tab-")
        cls.environment = patch.dict(os.environ, {"TTB_DATA_DIR": cls.directory.name, "QT_QPA_PLATFORM": "offscreen"})
        cls.environment.start()
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        import trading_tools_ui
        import account_store
        from reward_history import RewardHistory
        cls.tab_mod, cls.stores, cls.History = trading_tools_ui, account_store, RewardHistory

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()
        cls.directory.cleanup()

    def tab(self, seed):
        directory = tempfile.mkdtemp(dir=self.directory.name)
        store = self.stores.AccountStore(Path(directory) / "acc.json")
        store.increment_inventory_item("lex_prime_barrel", "Lex Prime Barrel", quantity=3)
        notes = []
        with patch.object(self.tab_mod, "_market_item_names", return_value={"Braton Prime Stock": "braton_prime_stock"}):
            tab = self.tab_mod.RewardFailuresTab(store, lambda m, s="info", ms=0: notes.append(m))
            tab.history = self.History(Path(directory) / "h.json")
            seed(tab.history)
            tab.reload()
        self.addCleanup(tab.deleteLater)
        return tab, store, notes

    @staticmethod
    def seed_two(history):
        history.record("a", ROWS)
        history.mark_failed("a")
        history.record("b", ROWS[:1])
        history.mark_failed("b", unread=[{"slot": 2, "text": "Cedo Pr", "candidates": [
            {"display_name": "Cedo Prime Barrel", "url_name": "cedo_prime_barrel"}]}])

    @staticmethod
    def texts(tab):
        from PySide6.QtWidgets import QLabel, QPushButton
        labels = [w.text() for w in tab.body.findChildren(QLabel)]
        buttons = [w.text() for w in tab.body.findChildren(QPushButton)]
        return labels, buttons

    def test_each_failure_is_its_own_page_starting_at_the_newest(self):
        tab, _store, _notes = self.tab(self.seed_two)
        self.assertEqual([s["failure_no"] for s in tab._failures], [1, 2])
        self.assertEqual(tab.current()["id"], "b")
        tab.go_to(0)
        self.assertEqual(tab.current()["id"], "a")

    def test_cards_show_plat_ducats_advice_owned_count_and_set_progress(self):
        tab, _store, _notes = self.tab(self.seed_two)
        tab.go_to(0)
        labels, buttons = self.texts(tab)
        joined = "\n".join(labels)
        self.assertIn("12p", joined)
        self.assertIn("45 ducats", joined)
        self.assertIn("Best pick", joined)
        self.assertIn("Save for ducats", joined)
        self.assertIn("Own x3", joined)                       # the Lex barrel is in the Item List
        self.assertIn("Own x0", joined)
        self.assertIn("2/4 parts", joined)
        self.assertIn("Vaulted", joined)
        self.assertEqual(buttons.count("I picked this"), 2)

    def test_a_card_that_could_not_be_read_offers_the_names_it_might_be(self):
        tab, _store, _notes = self.tab(self.seed_two)
        labels, buttons = self.texts(tab)
        self.assertIn("Couldn't read this card", labels)
        self.assertIn("Read as: Cedo Pr", labels)
        self.assertIn("Cedo Prime Barrel", buttons)

    def test_picking_adds_the_item_marks_the_failure_fixed_and_moves_on(self):
        tab, store, notes = self.tab(self.seed_two)
        tab.go_to(0)
        tab.pick("braton_prime_stock", "Braton Prime Stock")
        self.assertEqual(store.items["braton_prime_stock"].quantity, 1)
        self.assertEqual(sum(row["quantity"] for row in store.get_reward_queue().values()), 1)
        self.assertEqual([s["failure_no"] for s in tab._failures], [2])
        self.assertTrue(any("Braton Prime Stock" in note for note in notes))

    def test_picking_an_unread_card_candidate_works_the_same_way(self):
        tab, store, _notes = self.tab(self.seed_two)
        tab.pick("cedo_prime_barrel", "Cedo Prime Barrel")
        self.assertEqual(store.items["cedo_prime_barrel"].quantity, 1)
        self.assertEqual([s["failure_no"] for s in tab._failures], [1])

    def test_typing_a_name_picks_something_else_and_rejects_unknown_names(self):
        tab, store, notes = self.tab(self.seed_two)
        tab.search.setText("not a real item")
        tab._pick_searched()
        self.assertNotIn("braton_prime_stock", store.items)
        tab.search.setText("braton prime stock")
        tab._pick_searched()
        self.assertEqual(store.items["braton_prime_stock"].quantity, 1)

    def test_nothing_to_add_closes_the_failure_without_touching_the_item_list(self):
        tab, store, _notes = self.tab(self.seed_two)
        before = dict(store.items)
        tab._dismiss()
        self.assertEqual([s["failure_no"] for s in tab._failures], [1])
        self.assertEqual(store.items.keys(), before.keys())

    def test_an_empty_tab_explains_itself_and_disables_the_actions(self):
        tab, _store, _notes = self.tab(lambda history: None)
        self.assertIsNone(tab.current())
        self.assertFalse(tab.pick_other.isEnabled())
        self.assertFalse(tab.dismiss.isEnabled())

    def test_the_screenshot_button_only_works_when_one_was_saved(self):
        def seed(history):
            history.record("a", ROWS)
            history.images_dir.mkdir(parents=True, exist_ok=True)
            (history.images_dir / "failure_a.jpg").write_bytes(b"not really a jpeg")
            history.mark_failed("a", image="failure_a.jpg")
            history.record("b", ROWS)
            history.mark_failed("b")
        tab, _store, _notes = self.tab(seed)
        self.assertFalse(tab.show_image.isEnabled())          # newest page has no screenshot
        tab.go_to(0)
        self.assertTrue(tab.show_image.isEnabled())


if __name__ == "__main__":
    unittest.main()
