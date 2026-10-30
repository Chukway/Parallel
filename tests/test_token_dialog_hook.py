import os
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton

import qt_ui
import token_capture


class FakeCaptureDialog:
    def __init__(self, on_success):
        self.on_success = on_success
        self.ran = False

    def exec(self):
        self.ran = True
        self.on_success()


class TokenDialogHookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_auto_button_runs_capture_and_saves_through_normal_callback(self):
        saved = []
        dialog = qt_ui.TokenDialog(lambda token: saved.append(token) or "Token saved and authenticated")
        buttons = [b.text() for b in dialog.findChildren(QPushButton)]
        self.assertIn("Get token automatically", buttons)

        holder = {}

        def fake_open(parent=None, on_success=None):
            holder["dialog"] = FakeCaptureDialog(on_success)
            return holder["dialog"]

        with patch.object(token_capture, "open_best_dialog", fake_open), \
                patch.object(qt_ui.config, "WFM_TOKEN", "eyJ.fake.token", create=True):
            dialog._get_token_auto()
            deadline = time.time() + 3
            while not saved and time.time() < deadline:
                self.app.processEvents()
                time.sleep(0.02)
        self.assertTrue(holder["dialog"].ran)
        self.assertEqual(saved, ["eyJ.fake.token"])

    def test_missing_helper_shows_message_instead_of_crashing(self):
        dialog = qt_ui.TokenDialog(lambda token: "ok")
        with patch.object(token_capture, "open_best_dialog", lambda *a, **k: None), \
                patch.object(qt_ui.QMessageBox, "information") as info:
            dialog._get_token_auto()
        info.assert_called_once()


if __name__ == "__main__":
    unittest.main()
