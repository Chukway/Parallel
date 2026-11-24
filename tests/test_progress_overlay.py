"""The dot progress pill: its state rules, its settings, and the fissure hooks."""
import ast
import os
from pathlib import Path
import tempfile
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


def method_of(class_name, name):
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    node = next(item for item in cls.body if isinstance(item, ast.FunctionDef) and item.name == name)
    namespace = {"print": lambda *a, **k: None}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "main.py", "exec"), namespace)
    return namespace[name]


class OverlayStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="overlay-test-")
        cls.environment = patch.dict(os.environ, {"TTB_DATA_DIR": cls.directory.name, "QT_QPA_PLATFORM": "offscreen"})
        cls.environment.start()
        import progress_overlay
        from PySide6.QtWidgets import QApplication
        cls.mod = progress_overlay
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()
        cls.directory.cleanup()

    def pill(self, **kw):
        overlay = self.mod.StepOverlay("FISSURE", ("Read", "Picked", "Confirmed", "Queued"), **kw)
        self.addCleanup(overlay.deleteLater)
        return overlay

    def test_lighting_a_step_lights_the_ones_before_it(self):
        overlay = self.pill()
        overlay.set_state(2, "done")
        self.assertEqual(overlay.states, ["done", "done", "done", "pending"])

    def test_failing_a_step_clears_the_ones_after_it(self):
        overlay = self.pill()
        overlay.set_state(3, "done")
        overlay.set_state(1, "failed", "Pick not confirmed")
        self.assertEqual(overlay.states, ["done", "failed", "pending", "pending"])
        self.assertEqual(overlay.note, "Pick not confirmed")

    def test_repeated_done_keeps_the_first_caption(self):
        overlay = self.pill()
        overlay.set_state(0, "done", "4 rewards read")
        overlay.set_state(1, "done", "Picked: Braton Prime Stock")
        overlay.set_state(0, "done", "4 rewards read")
        self.assertEqual(overlay.note, "Picked: Braton Prime Stock")

    def test_reset_puts_every_dot_back(self):
        overlay = self.pill()
        overlay.set_state(3, "done", "Added")
        overlay.reset()
        self.assertEqual(overlay.states, ["pending"] * 4)
        self.assertEqual(overlay.note, "")

    def test_bad_index_or_state_is_ignored(self):
        overlay = self.pill()
        overlay.set_state(9, "done")
        overlay.set_state(0, "bogus")
        self.assertEqual(overlay.states, ["pending"] * 4)

    def test_only_visible_while_its_feature_is_on(self):
        overlay = self.pill()
        overlay.set_active(False)
        overlay.set_state(0, "done")
        self.assertFalse(overlay.isVisible())
        overlay.set_active(True)
        self.assertTrue(overlay.isVisible())
        overlay.set_active(False)
        self.assertFalse(overlay.isVisible())

    def test_hide_after_keeps_it_hidden_until_something_happens(self):
        overlay = self.pill(hide_after=5)
        overlay.set_active(True)
        self.assertFalse(overlay.isVisible())
        overlay.set_state(0, "done")
        self.assertTrue(overlay.isVisible())

    def test_each_corner_stays_on_screen(self):
        area = self.app.primaryScreen().availableGeometry()
        for corner in self.mod.CORNERS:
            overlay = self.pill(corner=corner)
            frame = overlay.frameGeometry()
            self.assertTrue(area.contains(frame), corner)
        right = self.pill(corner="top_right")
        left = self.pill(corner="top_left")
        self.assertGreater(right.x(), left.x())

    def test_unknown_corner_falls_back_to_top_right(self):
        self.assertEqual(self.mod.clean_corner("middle"), "top_right")

    def test_words_make_it_wider(self):
        self.assertGreater(self.pill(show_labels=True).width(), self.pill(show_labels=False).width())


class OverlaySettingsTests(unittest.TestCase):
    def test_valid_values_are_kept_and_bad_ones_dropped(self):
        from settings_validation import sanitize_settings
        clean = sanitize_settings({
            "step_overlay_enabled": False, "step_overlay_labels": False,
            "step_overlay_corner": "bottom_left", "step_overlay_hide_seconds": 9999,
        })
        self.assertEqual(clean["step_overlay_corner"], "bottom_left")
        self.assertIs(clean["step_overlay_enabled"], False)
        self.assertEqual(clean["step_overlay_hide_seconds"], 120)
        bad = sanitize_settings({"step_overlay_corner": "middle", "step_overlay_enabled": "yes"})
        self.assertNotIn("step_overlay_corner", bad)
        self.assertNotIn("step_overlay_enabled", bad)


class FissureHookTests(unittest.TestCase):
    def app(self):
        return SimpleNamespace(ui=Mock(), _pending_reward_pick=None, _pending_reward_slot=None,
                               _prepare_reward_review_async=Mock())

    def test_picking_a_reward_lights_the_second_dot(self):
        app = self.app()
        update = MethodType(method_of(self._cls(), "_update_pending_reward_from_rows"), app)
        update(2, [{"display_name": "Braton Prime Stock", "url_name": "braton_prime_stock", "slot_index": 2}])
        app.ui.overlay_step.assert_any_call("fissure", 1, "done", "Picked: Braton Prime Stock")

    def test_losing_the_pick_dims_the_second_dot(self):
        app = self.app()
        app._pending_reward_pick = {"slot_index": 1, "url_name": "x"}
        update = MethodType(method_of(self._cls(), "_update_pending_reward_from_rows"), app)
        update(None, [])
        app.ui.overlay_step.assert_called_with("fissure", 1, "pending")

    def test_a_missing_ui_never_breaks_the_reward_flow(self):
        app = SimpleNamespace(_pending_reward_pick=None, _pending_reward_slot=None,
                              _prepare_reward_review_async=Mock())
        update = MethodType(method_of(self._cls(), "_update_pending_reward_from_rows"), app)
        update(2, [{"display_name": "Braton Prime Stock", "url_name": "braton_prime_stock", "slot_index": 2}])
        self.assertEqual(app._pending_reward_pick["url_name"], "braton_prime_stock")

    def _cls(self):
        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        return next(node.name for node in tree.body if isinstance(node, ast.ClassDef)
                    and any(isinstance(i, ast.FunctionDef) and i.name == "_update_pending_reward_from_rows"
                            for i in node.body))


if __name__ == "__main__":
    unittest.main()
