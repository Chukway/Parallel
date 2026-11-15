import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pillar2_ocr import MatchedItem
from reward_scanner import RewardScanner
from set_catalog import SetCatalog


def _catalog(tmp: Path) -> SetCatalog:
    catalog = SetCatalog(tmp / "sets.json")
    catalog._store_result("bow_prime_set", {
        "set_slug": "bow_prime_set",
        "set_name": "Bow Prime Set",
        "members": [
            {"slug": "bow_prime_set", "name": "Bow Prime Set", "needed": 1},
            {"slug": "bow_prime_blade", "name": "Bow Prime Blade", "needed": 1},
            {"slug": "bow_prime_grip", "name": "Bow Prime Grip", "needed": 1},
            {"slug": "bow_prime_blueprint", "name": "Bow Prime Blueprint", "needed": 1},
        ],
    })
    return catalog


def _app(tmp: Path, listed: bool = True):
    from main import App
    listing = SimpleNamespace(quantity=1, platinum=20, order_id="o1", visible=True)
    api = SimpleNamespace(get_item_id=lambda slug: slug)
    existing = {"bow_prime_set": listing} if listed else {}
    app = SimpleNamespace(_set_catalog=_catalog(tmp), api=api)
    return app, existing, App._combine_parts_into_sets


class CombinePartsIntoSetsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_full_set_of_parts_becomes_one_set_sale(self):
        app, existing, combine = _app(self.tmp)
        result = combine(app, {"bow_prime_blade": 1, "bow_prime_grip": 1, "bow_prime_blueprint": 1}, existing)
        self.assertEqual(dict(result), {"bow_prime_set": 1})

    def test_extra_part_stays_a_part_sale(self):
        app, existing, combine = _app(self.tmp)
        result = combine(app, {"bow_prime_blade": 1, "bow_prime_grip": 2, "bow_prime_blueprint": 1}, existing)
        self.assertEqual(dict(result), {"bow_prime_set": 1, "bow_prime_grip": 1})

    def test_incomplete_set_is_left_as_parts(self):
        app, existing, combine = _app(self.tmp)
        sold = {"bow_prime_blade": 1, "bow_prime_grip": 1}
        self.assertEqual(dict(combine(app, sold, existing)), sold)

    def test_no_set_listing_means_parts_are_sold_as_parts(self):
        app, existing, combine = _app(self.tmp, listed=False)
        sold = {"bow_prime_blade": 1, "bow_prime_grip": 1, "bow_prime_blueprint": 1}
        self.assertEqual(dict(combine(app, sold, existing)), sold)


class WrappedNameTieTests(unittest.TestCase):
    def _scanner(self):
        names = {
            "Sevagoth Prime Blueprint": "sevagoth_prime_blueprint",
            "Sevagoth Prime Neuroptics Blueprint": "sevagoth_prime_neuroptics_blueprint",
        }
        return SimpleNamespace(items_cache=SimpleNamespace(url_name=lambda name: names.get(name)))

    def test_longer_name_wins_when_every_word_is_present(self):
        matched = MatchedItem(
            "Sevagoth Prime Blueprint", "sevagoth_prime_blueprint", 90,
            "Sevagoth Prime Blueprint Neuroptics",
            alternatives=[("Sevagoth Prime Neuroptics Blueprint", 90)],
        )
        resolved = RewardScanner._resolve_wrapped_tie(
            self._scanner(), matched, "Sevagoth Prime Blueprint Neuroptics"
        )
        self.assertEqual(resolved.display_name, "Sevagoth Prime Neuroptics Blueprint")
        self.assertEqual(resolved.url_name, "sevagoth_prime_neuroptics_blueprint")

    def test_stays_ambiguous_when_the_extra_word_is_missing(self):
        matched = MatchedItem(
            "Sevagoth Prime Blueprint", "sevagoth_prime_blueprint", 90, "Sevagoth Prime",
            alternatives=[("Sevagoth Prime Neuroptics Blueprint", 90)],
        )
        self.assertIsNone(
            RewardScanner._resolve_wrapped_tie(self._scanner(), matched, "Sevagoth Prime")
        )


if __name__ == "__main__":
    unittest.main()
