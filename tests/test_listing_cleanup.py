import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from account_store import AccountStore, StoredItem, StoredListing
import qt_ui

_app = QApplication.instance() or QApplication([])


def _store() -> AccountStore:
    store = AccountStore(Path(tempfile.mkdtemp()) / "account_state.json")
    for slug, name in (("velox_prime_blueprint", "Velox Prime Blueprint"), ("trumna_prime_barrel", "Trumna Prime Barrel")):
        store.items[slug] = StoredItem(name, slug, quantity=1)
        store.listings[slug] = StoredListing(name, slug, "order-" + slug, "id", 5, 1, True, 0.0)
    return store


class DeleteAndForgetTests(unittest.TestCase):
    def _run(self, forget: bool, still_listed=()):
        store = _store()
        fake = SimpleNamespace(store=store)

        def delete() -> str:
            for slug in list(store.listings):
                if slug not in still_listed:
                    del store.listings[slug]
            return "Deleted."

        message = qt_ui.Dashboard._delete_and_forget(
            fake, delete, ["velox_prime_blueprint", "trumna_prime_barrel"], forget
        )()
        return store, message

    def test_items_of_deleted_listings_leave_my_items(self):
        store, message = self._run(forget=True)
        self.assertEqual(store.items, {})
        self.assertIn("Removed 2", message)

    def test_unchecked_box_keeps_the_items(self):
        store, _message = self._run(forget=False)
        self.assertEqual(len(store.items), 2)

    def test_items_whose_delete_failed_are_kept(self):
        store, _message = self._run(forget=True, still_listed=("trumna_prime_barrel",))
        self.assertEqual(list(store.items), ["trumna_prime_barrel"])


class RemoveListedItemsTests(unittest.TestCase):
    def _page(self):
        store = _store()
        page = qt_ui.OwnedInventoryPage(store, lambda *args: None)
        deleted = []

        def delete_listings(urls):
            deleted.extend(urls)
            for url in urls:
                store.listings.pop(url, None)
            return "Deleted."

        page.delete_listings = delete_listings
        page.table.selectAll()
        return page, store, deleted

    def test_yes_to_both_questions_deletes_the_listings_and_the_items(self):
        page, store, deleted = self._page()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
                patch.object(page, "_delete_listings_then_forget",
                             side_effect=lambda slugs, listed: page._finish_removal(page.delete_listings(listed), slugs)):
            page._remove_selected_items()
        self.assertEqual(sorted(deleted), ["trumna_prime_barrel", "velox_prime_blueprint"])
        self.assertEqual(store.items, {})

    def test_keeping_the_market_listings_removes_only_the_items(self):
        page, store, deleted = self._page()
        answers = iter([QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No])
        with patch.object(QMessageBox, "question", side_effect=lambda *a, **k: next(answers)):
            page._remove_selected_items()
        self.assertEqual(deleted, [])
        self.assertEqual(store.items, {})
        self.assertEqual(len(store.listings), 2)

    def test_item_is_kept_when_its_listing_could_not_be_deleted(self):
        page, store, _deleted = self._page()
        page._finish_removal("ERROR: nope", ["velox_prime_blueprint", "trumna_prime_barrel"])
        self.assertEqual(sorted(store.items), ["trumna_prime_barrel", "velox_prime_blueprint"])


class CheckListingsTests(unittest.TestCase):
    def _page(self):
        store = _store()
        notes = []
        page = qt_ui.OwnedInventoryPage(store, lambda message, status, ms: notes.append(message))
        page._listed_before = set(store.listings)
        del store.listings["velox_prime_blueprint"]   # sold on the market since
        return page, store, notes

    def test_gone_items_are_removed_when_confirmed(self):
        page, store, _notes = self._page()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            page._finish_check_listings("Refreshed 1 WFM sell listings.")
        self.assertNotIn("velox_prime_blueprint", store.items)
        self.assertIn("trumna_prime_barrel", store.items)

    def test_gone_items_stay_when_declined(self):
        page, store, _notes = self._page()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            page._finish_check_listings("Refreshed 1 WFM sell listings.")
        self.assertIn("velox_prime_blueprint", store.items)

    def test_nothing_to_remove_says_up_to_date(self):
        page, store, notes = self._page()
        page._listed_before = set(store.listings)
        page._finish_check_listings("Refreshed 1 WFM sell listings.")
        self.assertTrue(any("Up to date" in note for note in notes))

    def test_error_message_changes_nothing(self):
        page, store, _notes = self._page()
        with patch.object(QMessageBox, "question") as question:
            page._finish_check_listings("Not authenticated.")
        question.assert_not_called()
        self.assertEqual(len(store.items), 2)


if __name__ == "__main__":
    unittest.main()


_SOLD_PATCH = patch.object(qt_ui, "_ask_sold", return_value=[])


def setUpModule():
    _SOLD_PATCH.start()


def tearDownModule():
    _SOLD_PATCH.stop()
