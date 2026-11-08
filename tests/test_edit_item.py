import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from account_store import AccountStore, StoredItem
import qt_ui

_app = QApplication.instance() or QApplication([])

NAMES = {"Velox Prime Blueprint": "velox_prime_blueprint", "Trumna Prime Barrel": "trumna_prime_barrel"}


def _store() -> AccountStore:
    store = AccountStore(Path(tempfile.mkdtemp()) / "account_state.json")
    store.items["velox_prime_blueprint"] = StoredItem("Velox Prime Blueprint", "velox_prime_blueprint", 1, "abc")
    return store


class SetItemTests(unittest.TestCase):
    def test_add_new_item(self):
        store = _store()
        store.set_item(None, "trumna_prime_barrel", "Trumna Prime Barrel", 3)
        self.assertEqual(store.items["trumna_prime_barrel"].quantity, 3)

    def test_fix_quantity_keeps_item_id(self):
        store = _store()
        store.set_item("velox_prime_blueprint", "velox_prime_blueprint", "Velox Prime Blueprint", 4)
        self.assertEqual(store.items["velox_prime_blueprint"].quantity, 4)
        self.assertEqual(store.items["velox_prime_blueprint"].item_id, "abc")

    def test_rename_replaces_wrong_item(self):
        store = _store()
        store.set_item("velox_prime_blueprint", "trumna_prime_barrel", "Trumna Prime Barrel", 1)
        self.assertEqual(list(store.items), ["trumna_prime_barrel"])


class DialogTests(unittest.TestCase):
    def test_unknown_name_is_rejected(self):
        dialog = qt_ui.ItemEditDialog(None, NAMES)
        dialog.name_input.setText("Nonsense Item")
        dialog._accept()
        self.assertIsNone(dialog.result_item)

    def test_name_match_ignores_case(self):
        dialog = qt_ui.ItemEditDialog(None, NAMES, quantity=2)
        dialog.name_input.setText("velox prime blueprint")
        dialog._accept()
        self.assertEqual(dialog.result_item, ("velox_prime_blueprint", "Velox Prime Blueprint", 2))


if __name__ == "__main__":
    unittest.main()
