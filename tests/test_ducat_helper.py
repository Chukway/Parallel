"""The ducat helper: what it recommends, and what removal changes."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

RULES = {15: 1, 25: 2, 45: 3, 65: 6, 100: 10}


class Catalog:
    """Braton: stock and receiver, one of each makes the set."""
    def is_known(self, slug): return True
    def refresh(self, slugs): pass
    def set_slug_for(self, slug): return "braton_prime_set" if slug.startswith("braton") else None
    def status_for(self, set_slug, owned):
        parts = [SimpleNamespace(slug=s, needed=1, owned=owned.get(s, 0))
                 for s in ("braton_prime_stock", "braton_prime_receiver")]
        return SimpleNamespace(parts=parts, buildable=min(p.owned for p in parts))


class AssessTests(unittest.TestCase):
    def setUp(self):
        import ducat_helper
        self.d = ducat_helper

    def test_a_cheap_part_goes_to_ducats(self):
        verdict, use, reason = self.d.assess(3, 45, 2, rules=RULES)
        self.assertEqual((verdict, use), ("ducats", 3))
        self.assertIn("45 ducats", reason)

    def test_a_part_worth_more_platinum_is_never_recommended(self):
        verdict, use, _ = self.d.assess(3, 100, 11, rules=RULES)
        self.assertEqual((verdict, use), ("sell", 0))
        self.assertEqual(self.d.assess(1, 100, 10, rules=RULES)[0], "ducats")      # exactly at the limit

    def test_copies_needed_for_a_complete_set_are_kept(self):
        self.assertEqual(self.d.assess(3, 45, 2, reserved=1, rules=RULES)[:2], ("ducats", 2))
        self.assertEqual(self.d.assess(1, 45, 2, reserved=1, rules=RULES)[:2], ("keep", 0))

    def test_no_price_means_check_it_yourself(self):
        self.assertEqual(self.d.assess(2, 45, None, rules=RULES)[:2], ("check", 0))

    def test_an_unlisted_ducat_tier_uses_a_ratio(self):
        self.assertEqual(self.d.plat_limit(90, {}), 9)

    def test_only_complete_sets_reserve_parts(self):
        owned = {"braton_prime_stock": 3, "braton_prime_receiver": 2}
        self.assertEqual(self.d.reserved_for_sets("braton_prime_stock", owned, Catalog()), 2)
        self.assertEqual(self.d.reserved_for_sets("braton_prime_stock", {"braton_prime_stock": 3}, Catalog()), 0)
        self.assertEqual(self.d.reserved_for_sets("lex_prime_barrel", owned, Catalog()), 0)


class StoreCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="ducat-test-")
        cls.environment = patch.dict(os.environ, {"TTB_DATA_DIR": cls.directory.name, "QT_QPA_PLATFORM": "offscreen"})
        cls.environment.start()
        import account_store
        import ducat_helper
        cls.stores, cls.d = account_store, ducat_helper

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()
        cls.directory.cleanup()

    def store(self, items):
        store = self.stores.AccountStore(Path(tempfile.mkdtemp(dir=self.directory.name)) / "acc.json")
        for slug, count in items.items():
            store.increment_inventory_item(slug, slug.replace("_", " ").title(), quantity=count)
        return store

    def rows(self, store):
        ducats = {"braton_prime_stock": 45, "braton_prime_receiver": 45, "lex_prime_barrel": 15,
                  "saryn_prime_blueprint": 100, "mystery_prime_part": 25}
        prices = {"braton_prime_stock": 2, "braton_prime_receiver": 20, "lex_prime_barrel": 1,
                  "saryn_prime_blueprint": 40}

        def price(slug):
            if slug not in prices:
                raise RuntimeError("no market")
            return prices[slug]

        with patch.object(self.d.config, "DUCAT_BULK_PLAT_VALUES", RULES), patch("builtins.print"):
            return self.d.build_rows(store, ducats.get, price, Catalog())


class BuildRowsTests(StoreCase):
    def test_rows_are_judged_and_recommended_ones_come_first(self):
        store = self.store({"braton_prime_stock": 2, "braton_prime_receiver": 1, "lex_prime_barrel": 3,
                            "saryn_prime_blueprint": 1, "mystery_prime_part": 1, "braton_prime_set": 1,
                            "arcane_energize": 4})
        rows = self.rows(store)
        verdicts = {r.url_name: (r.verdict, r.use) for r in rows}
        self.assertEqual(verdicts["braton_prime_stock"], ("ducats", 1))       # one is kept for the set
        self.assertEqual(verdicts["braton_prime_receiver"][0], "sell")
        self.assertEqual(verdicts["lex_prime_barrel"], ("ducats", 3))
        self.assertEqual(verdicts["saryn_prime_blueprint"][0], "sell")
        self.assertEqual(verdicts["mystery_prime_part"][0], "check")
        self.assertNotIn("braton_prime_set", verdicts)                        # sets have no ducat value
        self.assertNotIn("arcane_energize", verdicts)
        self.assertEqual([r.verdict for r in rows[:2]], ["ducats", "ducats"])
        self.assertEqual(rows[0].url_name, "braton_prime_stock")              # 22 ducats per plat beats 15

    def test_a_failed_set_lookup_is_not_recommended(self):
        store = self.store({"lex_prime_barrel": 2})
        broken = Mock()
        broken.is_known.side_effect = RuntimeError("offline")
        with patch.object(self.d.config, "DUCAT_BULK_PLAT_VALUES", RULES), patch("builtins.print"):
            rows = self.d.build_rows(store, {"lex_prime_barrel": 15}.get, lambda s: 1, broken)
        self.assertEqual((rows[0].verdict, rows[0].use), ("check", 0))


class RemovalTests(StoreCase):
    def test_item_list_removal_takes_only_the_chosen_copies(self):
        store = self.store({"lex_prime_barrel": 3})
        self.assertEqual(self.d.remove_from_item_list(store, [("lex_prime_barrel", 2), ("not_owned", 1)]), 2)
        self.assertEqual(store.items["lex_prime_barrel"].quantity, 1)
        self.assertEqual(self.d.remove_from_item_list(store, [("lex_prime_barrel", 9)]), 1)

    def api(self):
        listing = lambda order, quantity: SimpleNamespace(order_id=order, platinum=4, quantity=quantity, visible=True)
        api = Mock()
        api.get_item_id.side_effect = lambda url: "id-" + url
        api.get_existing_sell_listing_map.return_value = {"id-a": listing("A", 3), "id-b": listing("B", 1),
                                                          "id-c": listing("C", 2)}
        api.delete_order.return_value = True
        return api

    def test_listings_are_lowered_deleted_or_skipped(self):
        api, store = self.api(), Mock()
        api.update_order_price.side_effect = [None, RuntimeError("rejected")]
        with patch("builtins.print"):
            result = self.d.remove_from_listings(
                api, store, [("a", 1), ("b", 5), ("zzz", 1), ("c", 1)], pause=lambda: None)
        self.assertEqual(result, {"lowered": ["a"], "deleted": ["b"], "not_listed": ["zzz"], "failed": ["c"]})
        self.assertEqual(api.update_order_price.call_args_list[0].args, ("A", 4))
        self.assertEqual(api.update_order_price.call_args_list[0].kwargs, {"quantity": 2, "visible": True})
        api.delete_order.assert_called_once_with("B")
        store.replace_listings.assert_called_once()

    def test_nothing_is_written_when_live_listing_is_off(self):
        api = self.api()
        with patch.object(self.d.config, "ENABLE_LIVE_LISTING", False):
            with self.assertRaises(RuntimeError):
                self.d.remove_from_listings(api, Mock(), [("a", 1)])
        api.delete_order.assert_not_called()


class DialogTests(StoreCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        import trading_tools_ui
        cls.ui = trading_tools_ui

    def dialog(self):
        store = self.store({"braton_prime_stock": 2, "braton_prime_receiver": 1, "lex_prime_barrel": 3})
        notes = []
        dialog = self.ui.DucatHelperDialog(None, store, lambda: Mock(), lambda m, s="info", ms=0: notes.append(m),
                                           catalog=Catalog(), start=False)
        self.addCleanup(dialog.deleteLater)
        dialog._show_rows(self.rows(store), "")
        return dialog, store

    def test_only_recommended_parts_start_ticked(self):
        dialog, _ = self.dialog()
        picks = {entry.url_name: count for entry, count in dialog.picks()}
        self.assertEqual(picks, {"braton_prime_stock": 1, "lex_prime_barrel": 3})
        self.assertIn("4 part(s) for 90 ducats", dialog.summary.text())

    def test_saying_no_removes_nothing(self):
        dialog, store = self.dialog()
        with patch.object(self.ui.QMessageBox, "question", return_value=self.ui.QMessageBox.No):
            dialog.remove_from_list()
        self.assertEqual(store.items["lex_prime_barrel"].quantity, 3)

    def test_removing_takes_the_chosen_copies_once(self):
        dialog, store = self.dialog()
        with patch.object(self.ui.QMessageBox, "question", return_value=self.ui.QMessageBox.Yes):
            dialog.remove_from_list()
            dialog.remove_from_list()                      # pressing again must not take more
        self.assertEqual(store.items["braton_prime_stock"].quantity, 1)
        self.assertEqual(store.items["braton_prime_receiver"].quantity, 1)
        self.assertEqual(getattr(store.items.get("lex_prime_barrel"), "quantity", 0), 0)

    def test_ticking_a_valuable_part_warns_in_the_confirmation(self):
        dialog, _ = self.dialog()
        index = next(i for i, r in enumerate(dialog.rows) if r.url_name == "braton_prime_receiver")
        dialog.table.item(index, 0).setCheckState(self.ui.Qt.Checked)
        self.assertIn("sells for 20p!", dialog._describe(dialog.picks()))

    def test_the_baro_tab_has_the_button(self):
        tab = self.ui.BaroTab(lambda: None, lambda *a: None, self.store({}))
        self.addCleanup(tab.deleteLater)
        self.assertEqual(tab.ducat_button.text(), "Pick items for ducats")


if __name__ == "__main__":
    unittest.main()
