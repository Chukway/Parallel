import tempfile
import unittest
from pathlib import Path

from arcane_catalog import ArcaneCatalog, ArcaneInfo, ArcaneStock
from pillar3_api import ExistingListing, WarframeMarketAPI, ranked_key


class ArcaneStockTests(unittest.TestCase):
    def test_quantities_are_per_rank_and_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stock.json"
            stock = ArcaneStock(path)
            stock.add("arcane_energize", 0, 3)
            stock.add("arcane_energize", 5)
            stock.add("arcane_energize", 5)
            rows = {(r.slug, r.rank): r.quantity for r in ArcaneStock(path).sorted_rows()}
            self.assertEqual(rows, {("arcane_energize", 0): 3, ("arcane_energize", 5): 2})
            stock.set_quantity("arcane_energize", 0, 0)
            self.assertEqual(len(ArcaneStock(path).sorted_rows()), 1)

    def test_catalog_round_trip_and_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cat.json"
            catalog = ArcaneCatalog(path)
            self.assertTrue(catalog.is_stale())
            catalog.refresh(fetch=lambda: [ArcaneInfo("arcane_energize", "Arcane Energize", "id1", 5)])
            again = ArcaneCatalog(path)
            self.assertFalse(again.is_stale())
            self.assertEqual(again.names(), {"Arcane Energize": "arcane_energize"})
            self.assertEqual(again.info("arcane_energize").max_rank, 5)


class RankAwareApiTests(unittest.TestCase):
    def make_api(self, orders):
        api = WarframeMarketAPI.__new__(WarframeMarketAPI)
        api._price_cache = {}
        api.get_orders = lambda slug: orders
        return api

    def order(self, plat, rank, status="ingame", user="u"):
        return {"id": f"{plat}-{rank}-{user}", "type": "sell", "platinum": plat, "quantity": 1,
                "rank": rank, "visible": True, "user": {"status": status, "ingameName": user, "id": user}}

    def test_price_uses_only_matching_rank(self):
        orders = [self.order(20, 0, user="a"), self.order(22, 0, user="b"),
                  self.order(150, 5, user="c"), self.order(140, 5, user="d")]
        api = self.make_api(orders)
        self.assertEqual(api.get_price_info("x", rank=0).lowest_sell_price, 20)
        self.assertEqual(api.get_price_info("x", rank=5).lowest_sell_price, 140)

    def test_no_orders_at_rank_is_a_clear_error(self):
        api = self.make_api([self.order(20, 0)])
        with self.assertRaisesRegex(Exception, "rank 5"):
            api.get_price_info("x", rank=5)

    def test_ranked_listing_keys_do_not_collide(self):
        api = WarframeMarketAPI.__new__(WarframeMarketAPI)
        api.get_slug_for_item_id = lambda _id: None
        api.get_my_orders = lambda: {"sell_orders": [
            {"id": "o0", "itemId": "i", "itemSlug": "arcane_energize", "platinum": 10, "quantity": 1, "rank": 0},
            {"id": "o5", "itemId": "i", "itemSlug": "arcane_energize", "platinum": 140, "quantity": 1, "rank": 5},
        ]}
        listings = api.get_existing_sell_listing_map()
        self.assertEqual(listings[ranked_key("arcane_energize", 0)].order_id, "o0")
        self.assertEqual(listings[ranked_key("arcane_energize", 5)].order_id, "o5")
        self.assertIsInstance(listings["arcane_energize"], ExistingListing)

    def test_create_order_sends_rank_only_when_given(self):
        sent = []
        api = WarframeMarketAPI.__new__(WarframeMarketAPI)
        api._request = lambda method, endpoint, **kw: sent.append(kw["json"]) or {"data": {"id": "new"}}
        api.create_sell_order("i", 50, 1)
        api.create_sell_order("i", 50, 1, rank=5)
        self.assertNotIn("rank", sent[0])
        self.assertEqual(sent[1]["rank"], 5)


if __name__ == "__main__":
    unittest.main()
