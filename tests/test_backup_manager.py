import json
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

os.environ.setdefault("TTB_DATA_DIR", tempfile.mkdtemp())

import backup_manager
from account_store import AccountStore


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.data = self.tmp / "data"
        self.data.mkdir()
        (self.data / "account_state.json").write_text(json.dumps({"items": {"a": {"quantity": 2}}}), encoding="utf-8")
        (self.data / "mastery_list.json").write_text("{}", encoding="utf-8")
        (self.data / ".env").write_text("WFM_TOKEN=secret", encoding="utf-8")

    def test_token_excluded_by_default(self):
        out = backup_manager.create_backup(self.tmp / "b.zip", data_dir=self.data)
        with zipfile.ZipFile(out) as z:
            self.assertNotIn(".env", z.namelist())
            self.assertIn("account_state.json", z.namelist())

    def test_restore_round_trip_and_safety_copy(self):
        out = backup_manager.create_backup(self.tmp / "b.zip", data_dir=self.data)
        (self.data / "account_state.json").write_text("{}", encoding="utf-8")
        restored = backup_manager.restore_backup(out, data_dir=self.data)
        self.assertIn("account_state.json", restored)
        self.assertEqual(json.loads((self.data / "account_state.json").read_text())["items"]["a"]["quantity"], 2)
        self.assertTrue(list((self.data / "backups").glob("before-restore-*.zip")))

    def test_restore_ignores_unknown_and_unsafe_paths(self):
        evil = self.tmp / "evil.zip"
        with zipfile.ZipFile(evil, "w") as z:
            z.writestr(backup_manager.MANIFEST, json.dumps({"app": "Parallel"}))
            z.writestr("../escaped.json", "{}")
            z.writestr("other.txt", "x")
        self.assertEqual(backup_manager.restore_backup(evil, data_dir=self.data), [])
        self.assertFalse((self.tmp / "escaped.json").exists())

    def test_rejects_foreign_zip(self):
        foreign = self.tmp / "f.zip"
        with zipfile.ZipFile(foreign, "w") as z:
            z.writestr("x", "y")
        with self.assertRaises(ValueError):
            backup_manager.restore_backup(foreign, data_dir=self.data)

    def test_auto_backup_rate_limited_and_pruned(self):
        self.assertIsNotNone(backup_manager.auto_backup(self.data))
        self.assertIsNone(backup_manager.auto_backup(self.data))

    def test_sales_survive_save_and_load(self):
        path = self.tmp / "state.json"
        store = AccountStore(path)
        store.items["a"] = __import__("account_store").StoredItem("A", "a", quantity=3)
        store.decrement_after_trade("a", 2)
        again = AccountStore(path)
        self.assertEqual(again.sales[-1]["count"], 2)
        self.assertEqual(again.items["a"].quantity, 1)


if __name__ == "__main__":
    unittest.main()
