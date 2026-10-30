import tempfile
import unittest
from pathlib import Path

import set_catalog
from set_catalog import SetCatalog

KRONEN = {
    "set_slug": "kronen_prime_set",
    "set_name": "Kronen Prime Set",
    "members": [
        {"slug": "kronen_prime_set", "name": "Kronen Prime Set", "needed": 1},
        {"slug": "kronen_prime_blade", "name": "Kronen Prime Blade", "needed": 2},
        {"slug": "kronen_prime_blueprint", "name": "Kronen Prime Blueprint", "needed": 1},
        {"slug": "kronen_prime_handle", "name": "Kronen Prime Handle", "needed": 2},
    ],
}


class SetCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "sets.json"
        set_catalog.SET_REQUEST_DELAY = 0
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def fetch(self, slug):
        self.calls.append(slug)
        return KRONEN if slug.startswith("kronen") else None

    def test_status_counts_buildable_and_missing(self):
        catalog = SetCatalog(self.path)
        catalog.refresh(["kronen_prime_blade"], fetch=self.fetch)
        status = catalog.status_for(
            "kronen_prime_set",
            {"kronen_prime_blade": 3, "kronen_prime_blueprint": 1, "kronen_prime_handle": 1},
        )
        self.assertEqual(status.buildable, 0)
        self.assertEqual(status.parts_have, 4)
        self.assertEqual(status.parts_total, 5)
        self.assertEqual([p.slug for p in status.missing_parts], ["kronen_prime_handle"])
        status = catalog.status_for(
            "kronen_prime_set",
            {"kronen_prime_blade": 4, "kronen_prime_blueprint": 2, "kronen_prime_handle": 2},
        )
        self.assertEqual(status.buildable, 1)

    def test_one_request_covers_whole_set_and_non_sets_are_cached(self):
        catalog = SetCatalog(self.path)
        catalog.refresh(
            ["kronen_prime_blade", "kronen_prime_handle", "rubico_stuff"], fetch=self.fetch
        )
        self.assertEqual(self.calls, ["kronen_prime_blade", "rubico_stuff"])
        self.assertEqual(catalog.set_slug_for("kronen_prime_handle"), "kronen_prime_set")
        self.assertIsNone(catalog.set_slug_for("rubico_stuff"))
        self.calls.clear()
        reloaded = SetCatalog(self.path)
        reloaded.refresh(["kronen_prime_blade", "rubico_stuff"], fetch=self.fetch)
        self.assertEqual(self.calls, [])

    def test_failed_fetch_is_not_cached(self):
        catalog = SetCatalog(self.path)

        def boom(_slug):
            raise OSError("offline")

        catalog.refresh(["kronen_prime_blade"], fetch=boom)
        self.assertFalse(catalog.is_known("kronen_prime_blade"))


if __name__ == "__main__":
    unittest.main()
