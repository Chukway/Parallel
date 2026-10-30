import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import tutorial


class TutorialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = patch.object(tutorial.config, "USER_DATA_DIR", Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_not_seen_until_marked(self):
        self.assertFalse(tutorial.tutorial_seen())
        tutorial.mark_tutorial_seen()
        self.assertTrue(tutorial.tutorial_seen())

    def test_skip_closes_and_remembers(self):
        dialog = tutorial.TutorialDialog()
        self.assertTrue(dialog.skip_button.isVisibleTo(dialog))
        dialog.skip()
        self.assertTrue(tutorial.tutorial_seen())

    def test_skip_without_remembering_when_unchecked(self):
        dialog = tutorial.TutorialDialog()
        dialog.dont_show.setChecked(False)
        dialog.skip()
        self.assertFalse(tutorial.tutorial_seen())

    def test_walks_every_step_and_finishes(self):
        dialog = tutorial.TutorialDialog()
        count = len(dialog.steps)
        for _ in range(count - 1):
            dialog.next()
        self.assertEqual(dialog.next_button.text(), "Done")
        dialog.next()
        self.assertTrue(tutorial.tutorial_seen())

    def test_back_is_disabled_on_first_step(self):
        dialog = tutorial.TutorialDialog()
        self.assertFalse(dialog.back_button.isEnabled())
        dialog.next()
        self.assertTrue(dialog.back_button.isEnabled())

    def test_esc_counts_as_skip(self):
        dialog = tutorial.TutorialDialog()
        dialog.reject()
        self.assertTrue(tutorial.tutorial_seen())


if __name__ == "__main__":
    unittest.main()
