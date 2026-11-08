import tempfile
import unittest
from pathlib import Path

from account_store import AccountStore, StoredItem


class RemoveItemsTests(unittest.TestCase):
    def _store(self) -> AccountStore:
        folder = Path(tempfile.mkdtemp())
        store = AccountStore(folder / "account_state.json")
        for slug, name, qty in (
            ("vadarya_prime_blueprint", "Vadarya Prime Blueprint", 1),
            ("velox_prime_blueprint", "Velox Prime Blueprint", 2),
        ):
            store.items[slug] = StoredItem(name, slug, quantity=qty)
        store.save()
        return store

    def test_removes_only_the_named_items_and_saves(self):
        store = self._store()
        self.assertEqual(store.remove_items(["vadarya_prime_blueprint"]), 1)
        reloaded = AccountStore(store.path)
        self.assertNotIn("vadarya_prime_blueprint", reloaded.items)
        self.assertEqual(reloaded.items["velox_prime_blueprint"].quantity, 2)

    def test_unknown_items_are_ignored(self):
        store = self._store()
        self.assertEqual(store.remove_items(["nope", "nope"]), 0)
        self.assertEqual(len(store.items), 2)

    def test_listings_are_left_alone(self):
        store = self._store()
        store.remove_items(["velox_prime_blueprint", "vadarya_prime_blueprint"])
        self.assertEqual(store.listings, {})
        self.assertEqual(store.items, {})


if __name__ == "__main__":
    unittest.main()
