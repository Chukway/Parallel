import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from account_store import AccountStore, StoredItem
import qt_ui

_app = QApplication.instance() or QApplication([])


class SellSelectedTests(unittest.TestCase):
    def _page(self):
        store = AccountStore(Path(tempfile.mkdtemp()) / "account_state.json")
        for slug, name, qty in (
            ("velox_prime_blueprint", "Velox Prime Blueprint", 2),
            ("trumna_prime_barrel", "Trumna Prime Barrel", 1),
        ):
            store.items[slug] = StoredItem(name, slug, quantity=qty)
        notes = []
        page = qt_ui.OwnedInventoryPage(store, lambda message, status, ms: notes.append(message))
        sold = []
        page.sell_items = sold.append
        return page, notes, sold

    def test_asks_for_a_selection_first(self):
        page, notes, sold = self._page()
        page._sell_selected()
        self.assertEqual(sold, [])
        self.assertTrue(notes)

    def test_hands_the_selected_items_to_the_sell_callback(self):
        page, _notes, sold = self._page()
        page.table.selectAll()
        page._sell_selected()
        self.assertEqual(sorted(sold[0]), ["trumna_prime_barrel", "velox_prime_blueprint"])


if __name__ == "__main__":
    unittest.main()
