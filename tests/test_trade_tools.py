import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import trade_tools
from set_catalog import SetCatalog


def _points(prices, volume=2, start_hour=0):
    return [
        {"datetime": f"2026-11-06T{start_hour + i:02d}:00:00Z", "avg_price": p, "volume": volume}
        for i, p in enumerate(prices)
    ]


class VaultedTests(unittest.TestCase):
    def test_refresh_keeps_only_vaulted_and_caches_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "v.json"
            items = [{"slug": "a", "vaulted": True}, {"slug": "b"}]
            catalog = trade_tools.VaultedCatalog(path, fetch=lambda: items)
            self.assertTrue(catalog.refresh())
            self.assertTrue(catalog.is_vaulted("a"))
            self.assertFalse(catalog.is_vaulted("b"))
            again = trade_tools.VaultedCatalog(path, fetch=lambda: self.fail("should use cache"))
            self.assertFalse(again.refresh())
            self.assertTrue(again.is_vaulted("a"))


class TrendTests(unittest.TestCase):
    def test_rising_falling_and_flat(self):
        up = {"payload": {"statistics_closed": {"48hours": _points([10] * 6 + [14] * 6)}}}
        down = {"payload": {"statistics_closed": {"48hours": _points([14] * 6 + [10] * 6)}}}
        flat = {"payload": {"statistics_closed": {"48hours": _points([10] * 12)}}}
        self.assertEqual(trade_tools.trend_from_statistics(up)["direction"], "up")
        self.assertEqual(trade_tools.trend_from_statistics(down)["direction"], "down")
        self.assertEqual(trade_tools.trend_from_statistics(flat)["direction"], "flat")

    def test_too_little_data_gives_none(self):
        self.assertIsNone(trade_tools.trend_from_statistics({}))
        thin = {"payload": {"statistics_closed": {"48hours": _points([10, 12], volume=1)}}}
        self.assertIsNone(trade_tools.trend_from_statistics(thin))

    def test_price_trend_caches_results(self):
        trade_tools._trend_cache.clear()
        calls = []
        payload = {"payload": {"statistics_closed": {"48hours": _points([10] * 6 + [14] * 6)}}}

        def fetch(slug):
            calls.append(slug)
            return payload

        self.assertEqual(trade_tools.price_trend("x", fetch=fetch)["direction"], "up")
        trade_tools.price_trend("x", fetch=fetch)
        self.assertEqual(calls, ["x"])


class SetCompareTests(unittest.TestCase):
    def test_set_worth_more_than_parts(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = SetCatalog(Path(tmp) / "s.json")
            catalog._store_result("a_prime_set", {
                "set_slug": "a_prime_set", "set_name": "A Prime Set",
                "members": [
                    {"slug": "a_prime_set", "name": "A Prime Set", "needed": 1},
                    {"slug": "a_prime_blueprint", "name": "A Prime Blueprint", "needed": 1},
                    {"slug": "a_prime_barrel", "name": "A Prime Barrel", "needed": 2},
                ],
            })
            prices = {"a_prime_set": 50, "a_prime_blueprint": 10, "a_prime_barrel": 15}
            api = SimpleNamespace(get_price_info=lambda slug: SimpleNamespace(lowest_sell_price=prices[slug]))
            result = trade_tools.compare_set_vs_parts(api, catalog, "a_prime_set")
            self.assertEqual(result.parts_total, 40)
            self.assertIn("full set", result.verdict)
            prices["a_prime_set"] = 30
            self.assertIn("separately", trade_tools.compare_set_vs_parts(api, catalog, "a_prime_set").verdict)

    def test_missing_part_price_means_no_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = SetCatalog(Path(tmp) / "s.json")
            catalog._store_result("b_set", {
                "set_slug": "b_set", "set_name": "B Set",
                "members": [{"slug": "b_set", "name": "B Set", "needed": 1},
                            {"slug": "b_part", "name": "B Part", "needed": 1}],
            })

            def price(slug):
                if slug == "b_part":
                    raise RuntimeError("no sellers")
                return SimpleNamespace(lowest_sell_price=20)

            result = trade_tools.compare_set_vs_parts(SimpleNamespace(get_price_info=price), catalog, "b_set")
            self.assertIsNone(result.parts_total)
            self.assertEqual(result.verdict, "Not enough price data.")


class WatchTests(unittest.TestCase):
    def test_buy_and_sell_targets_with_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = trade_tools.WatchStore(Path(tmp) / "w.json")
            store.set("cheap", "Cheap", "below", 10)
            store.set("dear", "Dear", "above", 50)
            prices = {"cheap": 8, "dear": 40}
            hits = store.check(lambda s: prices[s], now=1000)
            self.assertEqual([e.slug for e in hits], ["cheap"])
            self.assertEqual(store.check(lambda s: prices[s], now=1100), [])      # cooldown
            later = store.check(lambda s: prices[s], now=1000 + trade_tools.WATCH_ALERT_COOLDOWN_SECONDS + 1)
            self.assertEqual([e.slug for e in later], ["cheap"])
            prices["dear"] = 55
            self.assertIn("dear", [e.slug for e in store.check(lambda s: prices[s], now=99999999)])

    def test_persists_and_removes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "w.json"
            store = trade_tools.WatchStore(path)
            store.set("x", "X", "below", 5)
            self.assertEqual(len(trade_tools.WatchStore(path).entries), 1)
            self.assertTrue(store.remove("x", "below"))
            self.assertEqual(len(trade_tools.WatchStore(path).entries), 0)

    def test_missing_price_never_alerts(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = trade_tools.WatchStore(Path(tmp) / "w.json")
            store.set("x", "X", "below", 5)
            self.assertEqual(store.check(lambda s: None, now=1), [])


class BuyTests(unittest.TestCase):
    def test_suggested_buy_price(self):
        self.assertEqual(trade_tools.suggest_buy_price(20, 12), 13)       # just above best buy order
        self.assertEqual(trade_tools.suggest_buy_price(20, None), 16)     # 80% of lowest sell
        self.assertEqual(trade_tools.suggest_buy_price(10, 15), 9)        # never at or above lowest sell
        self.assertEqual(trade_tools.suggest_buy_price(1, None), 1)
        self.assertIsNone(trade_tools.suggest_buy_price(None, None, None))

    def test_market_snapshot_uses_only_in_game_visible_orders(self):
        def order(kind, plat, status="ingame", visible=True):
            return {"type": kind, "platinum": plat, "visible": visible, "user": {"status": status}}

        orders = [order("sell", 10), order("sell", 14), order("sell", 5, "offline"),
                  order("sell", 3, visible=False), order("buy", 7), order("buy", 9, "offline")]
        snap = trade_tools.market_snapshot(SimpleNamespace(get_orders=lambda slug: orders), "x")
        self.assertEqual((snap["lowest_sell"], snap["avg_sell"], snap["top_buy"]), (10, 12.0, 7))
        self.assertEqual(snap["suggested_buy"], 8)

    def test_post_buy_order_creates_or_updates(self):
        from pillar3_api import WarframeMarketAPI
        api = object.__new__(WarframeMarketAPI)
        calls = []
        api.get_item_id = lambda slug: "id1"
        api._request = lambda method, path, **kw: calls.append((method, path, kw.get("json"))) or {"data": {"id": "new"}}
        api.update_order_price = lambda oid, price, quantity=1, visible=True: calls.append(("PATCH", oid, price)) or {"id": oid}
        api.get_my_orders = lambda: {"buy_orders": []}
        self.assertEqual(api.post_buy_order("item", 12, 2, visible=False)[0], "created")
        body = calls[0][2]
        self.assertEqual((body["type"], body["platinum"], body["quantity"], body["visible"]), ("buy", 12, 2, False))
        api.get_my_orders = lambda: {"buy_orders": [{"id": "old", "itemId": "id1"}]}
        self.assertEqual(api.post_buy_order("item", 15)[0], "updated")
        self.assertEqual(calls[-1], ("PATCH", "old", 15))


class RepriceTests(unittest.TestCase):
    def test_only_undercut_listings_are_returned(self):
        def listing(order_id, slug, plat):
            return SimpleNamespace(order_id=order_id, item_url_name=slug, platinum=plat,
                                   quantity=1, visible=True, rank=0)

        a, b = listing("1", "item_a", 20), listing("2", "item_b", 10)
        infos = {
            "item_a": SimpleNamespace(lowest_sell_price=15, suggested_listing_price=14),
            "item_b": SimpleNamespace(lowest_sell_price=10, suggested_listing_price=10),
        }
        api = SimpleNamespace(
            get_existing_sell_listing_map=lambda: {"item_a": a, "id1": a, "item_b": b},
            get_price_info=lambda slug, rank=None: infos[slug],
        )
        rows = trade_tools.build_reprice_rows(api, {"item_a": "Item A"})
        self.assertEqual([r.order_id for r in rows], ["1"])
        self.assertEqual(rows[0].suggested, 14)
        self.assertEqual(rows[0].undercut_by, 5)

    def test_apply_reports_errors(self):
        class Api:
            def update_order_price(self, order_id, price, quantity=1, visible=True):
                if order_id == "bad":
                    raise RuntimeError("nope")

        rows = [trade_tools.RepriceRow("ok", "a", "A", 1, True, 20, 15, 14),
                trade_tools.RepriceRow("bad", "b", "B", 1, True, 20, 15, 14)]
        done, errors = trade_tools.apply_reprice(Api(), rows)
        self.assertEqual(done, 1)
        self.assertEqual(len(errors), 1)


class SalesTests(unittest.TestCase):
    def test_summary_and_csv(self):
        now = 5_000_000.0
        sales = [
            {"url_name": "a", "display_name": "A", "count": 2, "at": now - 100, "platinum": 40},
            {"url_name": "b", "display_name": "B", "count": 1, "at": now - 3 * 86400, "platinum": 10},
            {"url_name": "a", "display_name": "A", "count": 1, "at": now - 20 * 86400, "platinum": 0},
        ]
        stats = trade_tools.sales_summary(sales, now=now)
        self.assertEqual((stats["today"], stats["week"], stats["platinum"]), (40, 50, 50))
        self.assertEqual(stats["unpriced"], 1)
        self.assertEqual(stats["top"][0]["name"], "A")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.csv"
            self.assertEqual(trade_tools.sales_to_csv(sales, path), 3)
            text = path.read_text(encoding="utf-8-sig")
            self.assertIn("Total platinum", text)
            self.assertIn(",2,40,20.0", text)


class BaroTests(unittest.TestCase):
    def test_rows_rank_by_plat_per_ducat_and_skip_untradable(self):
        data = {"inventory": [
            {"item": "Ash Prime Blueprint", "ducats": 100, "credits": 1000},
            {"item": "Fancy Skin", "ducats": 50, "credits": 500},
            {"item": "Nova Prime Blueprint", "ducats": 100, "credits": 1000},
        ]}
        names = {"Ash Prime Blueprint": "ash_prime_blueprint", "Nova Prime Blueprint": "nova_prime_blueprint"}
        prices = {"ash_prime_blueprint": 10, "nova_prime_blueprint": 30}
        rows = trade_tools.baro_rows(data, names, lambda s: prices[s])
        self.assertEqual([r["name"] for r in rows], ["Nova Prime Blueprint", "Ash Prime Blueprint", "Fancy Skin"])
        self.assertEqual(rows[0]["plat_per_100_ducats"], 30.0)
        self.assertIsNone(rows[2]["price"])

    def test_status_text(self):
        data = {"activation": "2026-11-06T14:00:00Z", "expiry": "2026-11-08T14:00:00Z", "location": "Kronia Relay"}
        here = trade_tools.baro_status(data, datetime(2026, 11, 7, 14, tzinfo=timezone.utc))
        self.assertIn("Kronia Relay", here)
        self.assertIn("Leaves in 1d 0h", here)
        self.assertIn("arrives", trade_tools.baro_status(data, datetime(2026, 11, 5, 14, tzinfo=timezone.utc)))


class ManualSaleTests(unittest.TestCase):
    def test_add_edit_delete_sale(self):
        from account_store import AccountStore
        with tempfile.TemporaryDirectory() as tmp:
            kwargs = {"path": Path(tmp) / "acc.json"} if "path" in AccountStore.__init__.__code__.co_varnames else {}
            store = AccountStore(**kwargs)
            store.set_item("x_prime_blueprint", "x_prime_blueprint", "X Prime Blueprint", 3)
            store.add_sale("x_prime_blueprint", "X Prime Blueprint", 2, 40, at=2000.0, take_from_items=True)
            store.add_sale("x_prime_blueprint", "X Prime Blueprint", 1, 10, at=1000.0)
            self.assertEqual([s["at"] for s in store.sales], [1000.0, 2000.0])      # kept in date order
            self.assertEqual(store.items["x_prime_blueprint"].quantity, 1)          # only the first took stock
            self.assertEqual(trade_tools.sales_summary(store.sales)["platinum"], 50)
            self.assertTrue(store.delete_sale(store.sales[0]))
            self.assertEqual(len(store.sales), 1)
            self.assertFalse(store.delete_sale({"url_name": "nope"}))


class SoldPromptTests(unittest.TestCase):
    def _page(self, tmp):
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        import qt_ui
        from account_store import AccountStore
        kwargs = {"path": Path(tmp) / "acc.json"} if "path" in AccountStore.__init__.__code__.co_varnames else {}
        store = AccountStore(**kwargs)
        store.set_item("a_prime_blueprint", "a_prime_blueprint", "A Prime Blueprint", 2)
        page = qt_ui.OwnedInventoryPage(store, lambda *args: None)
        page.reload()
        page.table.selectRow(0)
        return qt_ui, store, page

    def test_item_list_removal_records_the_sale(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            qt_ui, store, page = self._page(tmp)
            sold = [("a_prime_blueprint", "A Prime Blueprint", 2, 30)]
            with patch.object(qt_ui, "_ask_sold", return_value=sold), \
                 patch.object(qt_ui.QMessageBox, "question", return_value=qt_ui.QMessageBox.StandardButton.Yes):
                page._remove_selected_items()
            self.assertNotIn("a_prime_blueprint", store.items)
            self.assertEqual([(s["url_name"], s["count"], s["platinum"]) for s in store.sales],
                             [("a_prime_blueprint", 2, 30)])

    def test_not_sold_removes_without_a_sale(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            qt_ui, store, page = self._page(tmp)
            with patch.object(qt_ui, "_ask_sold", return_value=[]), \
                 patch.object(qt_ui.QMessageBox, "question", return_value=qt_ui.QMessageBox.StandardButton.Yes):
                page._remove_selected_items()
            self.assertNotIn("a_prime_blueprint", store.items)
            self.assertEqual(store.sales, [])

    def test_selling_one_of_two_keeps_the_other(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            qt_ui, store, page = self._page(tmp)
            sold = [("a_prime_blueprint", "A Prime Blueprint", 1, 15)]
            with patch.object(qt_ui, "_ask_sold", return_value=sold), \
                 patch.object(qt_ui.QMessageBox, "question") as question:
                page._remove_selected_items()
            question.assert_not_called()                      # nothing is being removed, so nothing to confirm
            self.assertEqual(store.items["a_prime_blueprint"].quantity, 1)
            self.assertEqual([(s["count"], s["platinum"]) for s in store.sales], [(1, 15)])

    def test_set_vs_parts_works_after_select_all(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            qt_ui, store, page = self._page(tmp)
            notes = []
            page._notify_cb = lambda message, status, ms: notes.append(message)
            page.get_api = lambda: object()
            page.table.clearSelection()
            page.table.setCurrentCell(-1, -1)
            page._toggle_select_all()
            self.assertEqual(page.select_all_button.text(), "Deselect all")
            page._compare_selected_set()
            self.assertFalse([m for m in notes if m.startswith("Select")])
            page._toggle_select_all()
            self.assertEqual(page.select_all_button.text(), "Select all")

    def test_cancelled_removal_logs_nothing(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            qt_ui, store, page = self._page(tmp)
            sold = [("a_prime_blueprint", "A Prime Blueprint", 2, 30)]
            with patch.object(qt_ui, "_ask_sold", return_value=sold), \
                 patch.object(qt_ui.QMessageBox, "question", return_value=qt_ui.QMessageBox.StandardButton.No):
                page._remove_selected_items()
            self.assertIn("a_prime_blueprint", store.items)
            self.assertEqual(store.sales, [])


class TradingPostPartialSaleTests(unittest.TestCase):
    def test_sold_one_of_two_lowers_the_listing_instead_of_deleting(self):
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        import qt_ui
        from types import SimpleNamespace
        listings = {
            "two": SimpleNamespace(quantity=2, platinum=40, visible=True),
            "one": SimpleNamespace(quantity=1, platinum=10, visible=True),
        }
        dash = SimpleNamespace(store=SimpleNamespace(listings=listings))
        to_delete, reduce = qt_ui.Dashboard._split_partial(
            dash, ["two", "one"], {"two": ("Two", 1, 20), "one": ("One", 1, 10)}
        )
        self.assertEqual(reduce, {"two": 1})            # 2 listed, 1 sold: leave 1 listed
        self.assertEqual(to_delete, ["one"])            # 1 listed, 1 sold: delete the listing


class RewardHistoryTests(unittest.TestCase):
    def test_record_pick_and_manual_add(self):
        from reward_history import RewardHistory
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "h.json"
            history = RewardHistory(path)
            rows = [{"display_name": "A Prime Blueprint", "url_name": "a_prime_blueprint", "price": 10, "ducats": 45, "slot_index": 0},
                    {"display_name": "B Prime Barrel", "url_name": "b_prime_barrel", "price": 5, "ducats": 15, "slot_index": 1}]
            history.record("s1", rows[:1])
            history.record("s1", rows)                        # later pass refreshes the same screen
            history.mark_picked("s1", "a_prime_blueprint")
            history.record("s2", rows)
            again = RewardHistory(path)
            self.assertEqual([s["id"] for s in again.recent()], ["s2", "s1"])      # newest first
            first = again.recent()[1]
            self.assertEqual(len(first["rewards"]), 2)
            self.assertEqual(first["picked"], "a_prime_blueprint")
            self.assertEqual(again.recent()[0]["picked"], "")

    def test_history_is_capped(self):
        from reward_history import RewardHistory, MAX_SCREENS
        with tempfile.TemporaryDirectory() as tmp:
            history = RewardHistory(Path(tmp) / "h.json")
            for i in range(MAX_SCREENS + 5):
                history.record(str(i), [{"display_name": "X", "url_name": "x"}])
            self.assertEqual(len(history.screens), MAX_SCREENS)

    def test_tab_adds_the_selected_reward_to_the_item_list(self):
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        from account_store import AccountStore
        from reward_history import RewardHistory
        from trading_tools_ui import RewardHistoryTab
        with tempfile.TemporaryDirectory() as tmp:
            kwargs = {"path": Path(tmp) / "acc.json"} if "path" in AccountStore.__init__.__code__.co_varnames else {}
            store = AccountStore(**kwargs)
            notes = []
            tab = RewardHistoryTab(store, lambda m, s="info", ms=0: notes.append(m))
            tab.history = RewardHistory(Path(tmp) / "h.json")
            tab.history.record("s1", [{"display_name": "A Prime Blueprint", "url_name": "a_prime_blueprint", "price": 10}])
            tab.reload()
            tab.table.setCurrentCell(0, 1)
            tab.add_selected()
            self.assertEqual(store.items["a_prime_blueprint"].quantity, 1)
            self.assertEqual(tab.history.recent()[0]["added"], "a_prime_blueprint")


class CombineSetTests(unittest.TestCase):
    def parts(self):
        return [
            SimpleNamespace(slug="blueprint", name="Blueprint", needed=1, owned=1),
            SimpleNamespace(slug="neuroptics", name="Neuroptics", needed=1, owned=1),
            SimpleNamespace(slug="chassis", name="Chassis", needed=1, owned=2),
            SimpleNamespace(slug="systems", name="Systems", needed=1, owned=2),
            SimpleNamespace(slug="blades", name="Blades", needed=2, owned=3),
        ]

    def test_plan_lowers_or_deletes_only_what_the_set_takes(self):
        listings = {
            "blueprint": SimpleNamespace(quantity=1),     # only copy goes into the set: delete the listing
            "chassis": SimpleNamespace(quantity=2),       # 2 listed, 1 left over: lower to 1
            "systems": SimpleNamespace(quantity=1),       # 1 listed, 1 left over: leave alone
            "blades": SimpleNamespace(quantity=3),        # set needs 2 of 3: lower to 1
        }
        changes = trade_tools.plan_set_combine(self.parts(), listings.get)
        result = {part.slug: new for part, _listing, new in changes}
        self.assertEqual(result, {"blueprint": 0, "chassis": 1, "blades": 1})

    def test_parts_needed_twice_delete_or_leave_the_extras(self):
        two_owned = SimpleNamespace(slug="stock", name="Stock", needed=2, owned=2)
        three_owned = SimpleNamespace(slug="stock", name="Stock", needed=2, owned=3)
        four_owned = SimpleNamespace(slug="stock", name="Stock", needed=2, owned=4)
        listed = SimpleNamespace(quantity=2)
        self.assertEqual([n for _p, _l, n in trade_tools.plan_set_combine([two_owned], lambda s: listed)], [0])   # both go in the set: delete
        three_listed = SimpleNamespace(quantity=3)
        self.assertEqual([n for _p, _l, n in trade_tools.plan_set_combine([three_owned], lambda s: three_listed)], [1])  # 3 owned: 1 extra stays listed
        self.assertEqual(trade_tools.plan_set_combine([four_owned], lambda s: listed), [])        # 4 owned, 2 listed: the set uses the 2 unlisted

    def test_unlisted_parts_need_no_change(self):
        self.assertEqual(trade_tools.plan_set_combine(self.parts(), lambda slug: None), [])

    def test_item_list_button_checks_the_set_is_complete(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        import qt_ui
        from account_store import AccountStore
        from set_catalog import SetCatalog
        with tempfile.TemporaryDirectory() as tmp:
            kwargs = {"path": Path(tmp) / "acc.json"} if "path" in AccountStore.__init__.__code__.co_varnames else {}
            store = AccountStore(**kwargs)
            store.set_item("x_prime_blueprint", "x_prime_blueprint", "X Prime Blueprint", 1)
            page = qt_ui.OwnedInventoryPage(store, lambda *args: None)
            page.catalog = SetCatalog(Path(tmp) / "s.json")
            page.catalog._store_result("x_prime_set", {
                "set_slug": "x_prime_set", "set_name": "X Prime Set",
                "members": [{"slug": "x_prime_set", "name": "X Prime Set", "needed": 1},
                            {"slug": "x_prime_blueprint", "name": "X Prime Blueprint", "needed": 1},
                            {"slug": "x_prime_barrel", "name": "X Prime Barrel", "needed": 1}],
            })
            notes, started = [], []
            page._notify_cb = lambda message, status, ms: notes.append(message)
            page.combine_set = started.append
            page.reload()
            page.table.selectRow(0)
            page._combine_selected_set()                     # barrel missing: refuse
            self.assertTrue(any("Missing" in n and "Barrel" in n for n in notes))
            self.assertEqual(started, [])
            store.set_item("x_prime_barrel", "x_prime_barrel", "X Prime Barrel", 1)
            page.reload()
            page.table.selectRow(0)
            with patch.object(qt_ui.QMessageBox, "question", return_value=qt_ui.QMessageBox.StandardButton.Yes):
                page._combine_selected_set()
            self.assertEqual(started, ["x_prime_set"])


class PageSmokeTests(unittest.TestCase):
    def test_trading_page_builds_with_no_api(self):
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        import qt_ui
        from account_store import AccountStore
        from trading_tools_ui import TradingToolsPage
        with tempfile.TemporaryDirectory() as tmp:
            store = AccountStore(Path(tmp) / "acc.json") if "path" in AccountStore.__init__.__code__.co_varnames else AccountStore()
            notes = []
            page = TradingToolsPage(store, lambda: None, lambda m, s="info", ms=0: notes.append(m))
            page.watchlist.timer.stop()
            page.on_shown()
            self.assertEqual(page.tabs.count(), 5)
            self.assertTrue(qt_ui.RewardPriceTag("Name", 10, 99, 0, 0, 100, vaulted=True,
                                                 trend={"direction": "up", "arrow": "▲"}))


if __name__ == "__main__":
    unittest.main()
