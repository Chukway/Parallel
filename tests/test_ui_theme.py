"""Theme palette, Settings theme/accent controls and the Dashboard navigation rail."""
import inspect
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import themes


class ThemePaletteTests(unittest.TestCase):
    ROLES = {"Background", "Panel", "Control", "Header", "Text", "Muted text",
             "Success", "Danger", "Info", "Accent", "Accent hover"}

    def test_every_theme_and_accent_builds_a_full_palette(self):
        for theme in themes.theme_names():
            for accent in themes.accent_names():
                palette = themes.build_palette(theme, accent)
                self.assertEqual(set(palette), self.ROLES, (theme, accent))
                self.assertNotIn("light", palette)

    def test_accent_uses_light_variant_only_for_light_themes(self):
        dark = themes.build_palette("Ion Midnight", "Cyan")
        light = themes.build_palette("Paper", "Cyan")
        self.assertEqual(dark["Accent"], themes.ACCENTS["Cyan"]["dark"][0])
        self.assertEqual(light["Accent"], themes.ACCENTS["Cyan"]["light"][0])
        self.assertEqual(themes.accent_swatch("Cyan", "Paper"), light["Accent"])

    def test_unknown_names_fall_back_to_defaults(self):
        self.assertEqual(themes.build_palette("nope", "nope"),
                         themes.build_palette(themes.DEFAULT_THEME, themes.DEFAULT_ACCENT))

    def test_text_is_legible_on_every_surface(self):
        def lum(colour):
            channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
            return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

        def contrast(a, b):
            hi, lo = sorted((lum(a), lum(b)), reverse=True)
            return (hi + 0.05) / (lo + 0.05)

        for theme in themes.theme_names():
            for accent in themes.accent_names():
                palette = themes.build_palette(theme, accent)
                for surface in ("Background", "Panel", "Control", "Header"):
                    self.assertGreaterEqual(contrast(palette["Text"], palette[surface]), 7, (theme, surface))
                    self.assertGreaterEqual(contrast(palette["Accent"], palette[surface]), 4.5,
                                            (theme, accent, surface))
                for surface in ("Background", "Panel", "Header"):
                    self.assertGreaterEqual(contrast(palette["Muted text"], palette[surface]), 4.5,
                                            (theme, surface))


class ThemeUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="parallel-theme-test-")
        cls.environment = patch.dict(os.environ, {"TTB_DATA_DIR": cls.directory.name, "QT_QPA_PLATFORM": "offscreen"})
        cls.environment.start()
        import qt_ui
        cls.ui = qt_ui
        cls.app = qt_ui.QApplication.instance() or qt_ui.QApplication([])

    @classmethod
    def tearDownClass(cls):
        cls.environment.stop()
        cls.directory.cleanup()

    def test_settings_theme_and_accent_controls_update_pending_values(self):
        dialog = self.ui.SettingsDialog()
        self.addCleanup(dialog.deleteLater)
        dialog.theme_combo.setCurrentText("Paper")
        dialog.accent_combo.setCurrentIndex(dialog.accent_combo.findData("Rose"))
        self.assertEqual(dialog._pending["ui_theme"], "Paper")
        self.assertEqual(dialog._pending["ui_accent"], "Rose")
        self.assertEqual(dialog._pending["ui_colors"], themes.build_palette("Paper", "Rose"))
        dialog._reset()
        self.assertEqual(dialog.theme_combo.currentText(), themes.DEFAULT_THEME)
        self.assertEqual(dialog.accent_combo.currentData(), themes.DEFAULT_ACCENT)
        self.assertEqual(dialog._pending["ui_theme"], themes.DEFAULT_THEME)
        self.assertEqual(dialog._pending["ui_accent"], themes.DEFAULT_ACCENT)

    def test_every_theme_applies_to_the_dashboard(self):
        ui = self.ui
        previous = ui.DEFAULT_UI_COLORS
        self.addCleanup(lambda: (setattr(ui, "DEFAULT_UI_COLORS", previous), ui._load_color_settings()))
        for theme in themes.theme_names():
            with patch.object(ui.config, "UI_COLORS", {}):
                ui.DEFAULT_UI_COLORS = themes.build_palette(theme, "Violet")
                ui._load_color_settings()
                ui._apply_app_style(self.app)
            self.assertEqual(ui.C_BG, themes.THEMES[theme]["Background"])
            self.assertIn(themes.build_palette(theme, "Violet")["Accent"], self.app.styleSheet())

    def dashboard(self):
        store = SimpleNamespace(listings={}, items={}, load=lambda: None, listed_rows=lambda: [])
        callbacks = {n: Mock(return_value="x") for n in inspect.signature(self.ui.Dashboard).parameters
                     if n.startswith("on_")}
        with patch.object(self.ui.config, "FIRST_LAUNCH", False):
            window = self.ui.Dashboard(store, **callbacks)
        self.addCleanup(window.deleteLater)
        return window

    def test_navigation_rail_keeps_the_tab_widget_api(self):
        window = self.dashboard()
        tabs = window.tabs
        self.assertEqual(tabs.count(), 4)
        self.assertEqual([tabs.tabText(i) for i in range(4)],
                         ["Inventory Management", "My Items & Sets", "Mastery List", "Arcanes"])
        self.assertEqual(tabs.currentIndex(), 0)
        seen = []
        tabs.currentChanged.connect(seen.append)
        tabs.setCurrentIndex(window.mastery_tab_index)
        self.assertEqual(seen, [window.mastery_tab_index])
        self.assertIs(tabs.stack.currentWidget(), window.mastery_page)
        window.owned_inventory  # page exists and is reachable via its index
        self.assertIs(tabs.stack.widget(window.owned_tab_index), window.owned_inventory)
        tabs.setCurrentIndex(window.mastery_tab_index)  # no repeat signal for the same page
        self.assertEqual(len(seen), 1)

    def test_set_progress_column_uses_ratio_for_sorting(self):
        item_class = self.ui._ProgressItem
        low, high = item_class("1/4 parts"), item_class("3/4 parts")
        low.setData(self.ui.Qt.UserRole + 1, 0.25)
        high.setData(self.ui.Qt.UserRole + 1, 0.75)
        self.assertTrue(low < high)
        self.assertFalse(high < low)


if __name__ == "__main__":
    unittest.main()
