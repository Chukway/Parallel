import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from account_store import AccountStore, StoredItem, StoredListing
import qt_ui

_app = QApplication.instance() or QApplication([])


def _page():
    store = AccountStore(Path(tempfile.mkdtemp()) / "account_state.json")
    for slug, name in (
        ("velox_prime_blueprint", "Velox Prime Blueprint"),
        ("trumna_prime_barrel", "Trumna Prime Barrel"),
        ("wisp_prime_chassis_blueprint", "Wisp Prime Chassis Blueprint"),
    ):
        store.items[slug] = StoredItem(name, slug, quantity=1)
    store.listings["velox_prime_blueprint"] = StoredListing(
        "Velox Prime Blueprint", "velox_prime_blueprint", "order", "id", 5, 1, True, 0.0
    )
    page = qt_ui.OwnedInventoryPage(store, lambda *args: None)
    sets = {"velox_prime_blueprint": "velox_prime_set", "trumna_prime_barrel": "trumna_prime_set"}
    page.catalog.set_slug_for = lambda url: sets.get(url)
    page.catalog.status_for = lambda slug, owned: None
    page.mastery_slugs = lambda: {"velox_prime_set"}
    page.reload()
    return page


def _names(page):
    return {
        page.table.item(row, 0).data(qt_ui.Qt.UserRole): page.table.item(row, 0).text()
        for row in range(page.table.rowCount())
    }


class ItemMarkTests(unittest.TestCase):
    def test_listed_items_get_a_dollar_and_mastery_items_a_star(self):
        names = _names(_page())
        self.assertEqual(names["velox_prime_blueprint"], "Velox Prime Blueprint  ★  $")
        self.assertEqual(names["trumna_prime_barrel"], "Trumna Prime Barrel")
        self.assertEqual(names["wisp_prime_chassis_blueprint"], "Wisp Prime Chassis Blueprint")

    def test_mastery_filter_shows_only_starred_items(self):
        page = _page()
        page.filter_combo.setCurrentText("On Mastery List")
        shown = [
            page.table.item(row, 0).data(qt_ui.Qt.UserRole)
            for row in range(page.table.rowCount())
            if not page.table.isRowHidden(row)
        ]
        self.assertEqual(shown, ["velox_prime_blueprint"])

    def test_listed_filter_still_works(self):
        page = _page()
        page.filter_combo.setCurrentText("Listed on market")
        shown = [
            page.table.item(row, 0).data(qt_ui.Qt.UserRole)
            for row in range(page.table.rowCount())
            if not page.table.isRowHidden(row)
        ]
        self.assertEqual(shown, ["velox_prime_blueprint"])


if __name__ == "__main__":
    unittest.main()
