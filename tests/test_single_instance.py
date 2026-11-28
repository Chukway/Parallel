"""Only one Parallel may run for each data folder."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

import single_instance

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "nt", "Windows mutex")
class SingleInstanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="one-copy-")
        self.addCleanup(self.directory.cleanup)

    def instance(self, folder=None):
        instance = single_instance.SingleInstance(folder or self.directory.name)
        self.addCleanup(instance.close)
        return instance

    def test_second_launch_is_not_first(self):
        self.assertTrue(self.instance().is_first)
        self.assertFalse(self.instance().is_first)

    def test_another_process_sees_the_running_copy(self):
        self.assertTrue(self.instance().is_first)
        code = ("import sys, single_instance;"
                "print(single_instance.SingleInstance(sys.argv[1]).is_first)")
        result = subprocess.run([sys.executable, "-B", "-c", code, self.directory.name],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.stdout.strip(), "False", result.stderr)

    def test_a_different_data_folder_runs_side_by_side(self):
        with tempfile.TemporaryDirectory() as other:
            self.assertTrue(self.instance().is_first)
            self.assertTrue(self.instance(other).is_first)

    def test_folder_spelling_does_not_matter(self):
        self.assertTrue(self.instance().is_first)
        self.assertFalse(self.instance(self.directory.name.upper()).is_first)

    def test_closing_the_first_copy_lets_a_new_one_start(self):
        first = single_instance.SingleInstance(self.directory.name)
        first.close()
        self.assertTrue(self.instance().is_first)

    def test_second_launch_brings_the_first_window_forward(self):
        first = self.instance()
        shown = threading.Event()
        first.listen(shown.set)
        second = self.instance()
        self.assertTrue(second.ask_first_to_show(timeout=5))
        self.assertTrue(shown.is_set())
        shown.clear()
        self.assertTrue(second.ask_first_to_show(timeout=5))        # works every time
        self.assertTrue(shown.is_set())

    def test_a_copy_that_does_not_answer_is_reported(self):
        self.instance()                                             # never listens: stuck or still starting
        self.assertFalse(self.instance().ask_first_to_show(timeout=0.3))


class StartupOrderTests(unittest.TestCase):
    def test_helper_processes_exit_before_the_one_copy_check(self):
        block = (ROOT / "main.py").read_text(encoding="utf-8").split('if __name__ == "__main__":')[1]
        order = [block.index(text) for text in
                 ("freeze_support()", '"--check-ocr"', "SingleInstance(config.USER_DATA_DIR)", "app = App()",
                  "instance.listen(app.ui.bring_to_front)")]
        self.assertEqual(order, sorted(order))


if __name__ == "__main__":
    unittest.main()
