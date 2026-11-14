import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from account_store import AccountStore, StoredItem, listing_key, split_listing_key
from pillar3_api import ExistingListing


def _listing(order, slug, plat, qty, rank):
    return ExistingListing(order_id=order, item_url_name=slug, item_id="id-" + slug, platinum=plat,
                           quantity=qty, visible=True, rank=rank)


def _store():
    return AccountStore(Path(tempfile.mkdtemp()) / "account_state.json")


class RankedListingTests(unittest.TestCase):
    def test_keys_round_trip(self):
        self.assertEqual(listing_key("arcane_energize", 0), "arcane_energize")
        self.assertEqual(listing_key("arcane_energize", 5), "arcane_energize@5")
        self.assertEqual(split_listing_key("arcane_energize@5"), ("arcane_energize", 5))
        self.assertEqual(split_listing_key("arcane_energize"), ("arcane_energize", 0))
        self.assertEqual(split_listing_key("name@home"), ("name@home", 0))

    def test_two_ranks_of_one_arcane_stay_separate_and_survive_a_reload(self):
        store = _store()
        store.items["arcane_energize"] = StoredItem("Arcane Energize", "arcane_energize", 3)
        store.replace_listings({
            "a": _listing("o0", "arcane_energize", 2, 1, 0),
            "b": _listing("o5", "arcane_energize", 140, 2, 5),
        })
        self.assertEqual(sorted(store.listings), ["arcane_energize", "arcane_energize@5"])
        ranked = store.listings["arcane_energize@5"]
        self.assertEqual((ranked.platinum, ranked.quantity, ranked.rank, ranked.slug), (140, 2, 5, "arcane_energize"))
        self.assertEqual(ranked.display_name, "Arcane Energize (rank 5)")
        self.assertEqual(store.listings["arcane_energize"].display_name, "Arcane Energize")
        store.load()
        self.assertEqual(store.listings["arcane_energize@5"].rank, 5)
        # The Item List still shows the arcane as listed (the highest-ranked listing counts).
        self.assertEqual(store.listing_for_item("arcane_energize").rank, 0)
        store.delete_listing("arcane_energize")
        self.assertEqual(store.listing_for_item("arcane_energize").rank, 5)

    def test_local_data_written_before_ranks_still_loads(self):
        store = _store()
        store.upsert_listing("velox_prime_blueprint", "Velox Prime Blueprint", "o1", platinum=5, quantity=1)
        self.assertEqual(store.listings["velox_prime_blueprint"].rank, 0)
        store.load()
        self.assertEqual(store.listings["velox_prime_blueprint"].slug, "velox_prime_blueprint")

    def test_selling_or_forgetting_a_ranked_listing_uses_the_item_slug(self):
        store = _store()
        store.items["primary_deadhead"] = StoredItem("Primary Deadhead", "primary_deadhead", 2)
        store.upsert_listing("primary_deadhead@5", "Primary Deadhead (rank 5)", "o", platinum=15, quantity=2)
        self.assertEqual(store.display_name("primary_deadhead@5"), "Primary Deadhead (rank 5)")
        store.add_sale("primary_deadhead@5", "Primary Deadhead (rank 5)", 1, 15, take_from_items=True)
        self.assertEqual(store.sales[-1]["url_name"], "primary_deadhead")
        self.assertEqual(store.items["primary_deadhead"].quantity, 1)
        self.assertEqual(store.remove_items(["primary_deadhead@5"]), 1)
        self.assertNotIn("primary_deadhead", store.items)

    def test_trade_matching_names_ignore_the_rank_label(self):
        store = _store()
        store.upsert_listing("primary_deadhead@5", "Primary Deadhead (rank 5)", "o", platinum=15, quantity=2)
        self.assertEqual(store.listed_match_candidates(), {"primary_deadhead": "Primary Deadhead"})

    def test_selling_a_set_takes_its_parts_out_of_the_item_list(self):
        store = _store()
        for slug, qty in (("khora_prime_blueprint", 1), ("khora_prime_chassis_blueprint", 2), ("khora_prime_set", 0)):
            store.items[slug] = StoredItem(slug, slug, qty)
        parts = [SimpleNamespace(slug="khora_prime_blueprint", needed=1),
                 SimpleNamespace(slug="khora_prime_chassis_blueprint", needed=1),
                 SimpleNamespace(slug="khora_prime_neuroptics_blueprint", needed=1)]   # not on the list: skipped
        catalog = SimpleNamespace(status_for=lambda slug, owned: SimpleNamespace(parts=parts) if slug == "khora_prime_set" else None)
        self.assertEqual(store.take_set_parts("khora_prime_set", 1, catalog), 2)
        self.assertEqual(store.items["khora_prime_blueprint"].quantity, 0)
        self.assertEqual(store.items["khora_prime_chassis_blueprint"].quantity, 1)
        self.assertEqual(store.take_set_parts("velox_prime_blueprint", 1, catalog), 0)   # not a set: nothing happens

    def test_trading_post_prices_a_ranked_listing_by_its_rank(self):
        import main
        calls = []
        api = SimpleNamespace(get_price_info=lambda slug, strategy, rank=None: calls.append((slug, rank)) or
                              SimpleNamespace(lowest_sell_price=7, suggested_listing_price=10, highest_sell_price=80))
        app = SimpleNamespace(api=api)
        result = main.App._preview_listing_prices(app, ["primary_deadhead@5", "velox_prime_blueprint"], "x")
        self.assertEqual(sorted(calls, key=str), [("primary_deadhead", 5), ("velox_prime_blueprint", None)])
        self.assertEqual(result["primary_deadhead@5"]["price"], 10)


if __name__ == "__main__":
    unittest.main()
