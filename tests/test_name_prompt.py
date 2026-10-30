import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("TTB_DATA_DIR", tempfile.mkdtemp())

from PySide6.QtWidgets import QApplication

import config
import name_prompt


class NamePromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.patches = [patch.object(config, "USER_DATA_DIR", self.tmp), patch.object(config, "WFM_INGAME_NAME", "")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_needed_only_when_name_empty(self):
        self.assertTrue(name_prompt.needs_name())
        with patch.object(config, "WFM_INGAME_NAME", "Chukway"):
            self.assertFalse(name_prompt.needs_name())

    def test_dont_ask_again_is_remembered(self):
        dialog = name_prompt.NamePromptDialog()
        dialog.never.setChecked(True)
        dialog._later()
        self.assertFalse(name_prompt.needs_name())

    def test_save_button_needs_text_and_returns_name(self):
        dialog = name_prompt.NamePromptDialog()
        self.assertFalse(dialog.save.isEnabled())
        dialog.field.setText("  Tenno  ")
        self.assertTrue(dialog.save.isEnabled())
        dialog._save()
        self.assertEqual(dialog.name, "Tenno")

    def test_saved_name_is_passed_to_callback(self):
        saved = []

        def fake_exec(self):
            self.name = "Tenno"
            return 1

        with patch.object(name_prompt.NamePromptDialog, "exec", fake_exec):
            self.assertTrue(name_prompt.ask_for_name_if_needed(None, saved.append))
        self.assertEqual(saved, ["Tenno"])

    def test_no_prompt_when_name_already_set(self):
        with patch.object(config, "WFM_INGAME_NAME", "Chukway"):
            self.assertFalse(name_prompt.ask_for_name_if_needed(None, lambda n: None))


if __name__ == "__main__":
    unittest.main()
