import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QCheckBox, QLineEdit, QVBoxLayout, QWidget

import tracker_data as td
import trackers_ui as tu

app = QApplication.instance() or QApplication([])


def sample_state():
    soon = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
    later = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat().replace("+00:00", "Z")
    recent = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat().replace("+00:00", "Z")
    return {
        "cetusCycle": {"state": "day", "expiry": soon},
        "vallisCycle": {"state": "warm", "expiry": soon},
        "cambionCycle": {"state": "fass", "expiry": soon},
        "earthCycle": {"state": "night", "expiry": soon},
        "duviriCycle": {"state": "joy", "expiry": soon,
                        "choices": [{"category": "normal", "choices": ["Nidus"]}, {"category": "hard", "choices": ["Boar"]}]},
        "fissures": [
            {"tier": "Lith", "tierNum": 1, "missionType": "Survival", "node": "Hydron (Sedna)", "expiry": soon, "isHard": False, "isStorm": False},
            {"tier": "Axi", "tierNum": 3, "missionType": "Defense", "node": "Ur (Uranus)", "expiry": soon, "isHard": True, "isStorm": False},
        ],
        "sortie": {"boss": "Vay Hek", "faction": "Grineer", "expiry": later,
                   "variants": [{"missionType": "Rescue", "node": "Unda (Venus)", "modifier": "Eximus Stronghold", "modifierDescription": "x"}]},
        "archonHunt": {"boss": "Archon Amar", "expiry": later, "missions": [{"type": "Extermination", "node": "Arval (Mars)"}]},
        "steelPath": {"currentReward": {"name": "Umbra Forma Blueprint", "cost": 150}, "expiry": later,
                      "rotation": [{"name": "Umbra Forma Blueprint"}, {"name": "50,000 Kuva"}]},
        "archimedeas": [
            {"typeKey": "C T_ L A B", "expiry": later, "personalModifiers": [{"name": "Gear Embargo", "description": "No gear."}],
             "missions": [{"faction": "Man in the Wall", "missionType": "Defense", "deviation": {"name": "Eroding Senses", "description": "x"},
                           "risks": [{"name": "Fortified Foes", "description": "y", "isHard": True}]}]},
            {"typeKey": "C T_ H E X", "expiry": later, "personalModifiers": [], "missions": []},
        ],
        "news": [{"message": "Hotfix 1", "link": "https://example.com/news", "date": recent}],
        "nightwave": {"season": 18, "activeChallenges": [{"title": "Kill things", "desc": "Kill 10", "isDaily": True, "reputation": 1000, "expiry": later}]},
        "voidTraders": [{"character": "Baro Ki'Teer", "location": "Kronia Relay (Earth)",
                         "activation": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat().replace("+00:00", "Z"),
                         "expiry": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat().replace("+00:00", "Z")}],
        "syndicateMissions": [], "dailyDeals": [], "alerts": [], "events": [], "invasions": [],
    }


class TrackerUiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        ws = td.WorldState(root / "ws.json")
        ws.data, ws.fetched = sample_state(), 1.0
        ws.fetched = __import__("time").time()
        self.hub = tu.TrackerHub(state=td.TrackerState(root / "t.json"), ws=ws)

    def tearDown(self):
        self.hub.stop()
        self.tmp.cleanup()

    def texts(self, page) -> str:
        from PySide6.QtWidgets import QLabel
        return "\n".join(label.text() for label in page.findChildren(QLabel))

    def test_live_page_shows_cycles_arbitration_baro_and_both_fissure_lists(self):
        page = tu.LivePage(self.hub)
        text = self.texts(page)
        for expected in ("World cycles", "Cetus", "Arbitration", "Void Trader", "Go to Baro section", "Full schedule",
                         "Normal (1)", "Steel Path (1)", "Hydron (Sedna)", "Ur (Uranus)"):
            self.assertIn(expected, text)
        self.assertNotIn("Bounties", text)  # off by default

    def test_arbitration_card_has_one_current_and_three_upcoming(self):
        page = tu.LivePage(self.hub)
        text = self.texts(page)
        self.assertEqual(text.count("· in "), 3)
        self.assertIn("ends in", text)

    def test_links_go_where_they_say(self):
        went = []
        self.hub.go_to_page = went.append
        baro = []
        self.hub.go_to_baro = lambda: baro.append(True)
        page = tu.LivePage(self.hub)
        from PySide6.QtWidgets import QLabel
        for label in page.findChildren(QLabel):
            if "Go to Baro section" in label.text():
                label.linkActivated.emit("#")
            if "Full schedule" in label.text():
                label.linkActivated.emit("#")
        self.assertEqual(baro, [True])
        self.assertEqual(went, ["arbitration"])

    def test_turning_a_card_on_or_off_updates_the_page(self):
        page = tu.LivePage(self.hub)
        self.hub.state.set_on("bounties", True)
        self.hub.card_changed()
        self.assertIn("Bounties", self.texts(page))
        self.hub.state.set_on("cycles", False)
        self.hub.card_changed()
        self.assertNotIn("World cycles", self.texts(page))

    def test_rename_a_card_and_a_page(self):
        page = tu.LivePage(self.hub)
        self.hub.state.set_title("cycles", "Open worlds")
        self.hub.state.set_page_title("live", "Right now")
        self.hub.card_changed()
        self.assertIn("Open worlds", self.texts(page))
        self.assertEqual(page.title.text(), "Right now")

    def test_weekly_and_daily_ticks_save_and_show_on_the_archimedea_page(self):
        weekly, archimedea, daily = tu.WeeklyPage(self.hub), tu.ArchimedeaPage(self.hub), tu.DailyPage(self.hub)
        eta = next(b for b in weekly.findChildren(QCheckBox) if "ETA" in b.text())
        eta.setChecked(True)
        self.assertTrue(self.hub.state.is_ticked("eta", "weekly"))
        done = [b for b in archimedea.findChildren(QCheckBox) if b.text() == "Done this week"]
        self.assertEqual(len(done), 2)
        self.assertTrue(any(b.isChecked() for b in done))  # the ETA card picked it up
        sortie = next(b for b in daily.findChildren(QCheckBox) if b.text() == "Sortie")
        sortie.setChecked(True)
        self.assertTrue(self.hub.state.is_ticked("sortie", "daily"))

    def test_archimedea_lists_debuffs_and_marks_elite_risks(self):
        text = self.texts(tu.ArchimedeaPage(self.hub))
        for expected in ("Deep Archimedea", "Temporal Archimedea", "Gear Embargo", "Fortified Foes", "Elite"):
            self.assertIn(expected, text)

    def test_other_pages_build(self):
        self.assertIn("Vay Hek", self.texts(tu.DailyPage(self.hub)))
        weekly = self.texts(tu.WeeklyPage(self.hub))
        for expected in ("Nidus", "Boar", "Archon Amar", "Umbra Forma Blueprint", "Next week: 50,000 Kuva"):
            self.assertIn(expected, weekly)
        self.assertIn("Hotfix 1", self.texts(tu.NewsPage(self.hub)))

    def test_missing_data_shows_friendly_cards_not_errors(self):
        self.hub.ws.data = {}
        for page_class in (tu.LivePage, tu.DailyPage, tu.WeeklyPage, tu.ArchimedeaPage, tu.NewsPage):
            page = page_class(self.hub)
            self.assertTrue(page.grid._cards, page_class.__name__)

    def test_arbitration_page_filters(self):
        page = tu.ArbitrationPage(self.hub)
        self.assertGreater(page.table.rowCount(), 20)
        page.tier_box.setCurrentIndex(page.tier_box.findData("S"))
        rows = {page.table.item(r, 5).text() for r in range(page.table.rowCount())}
        self.assertLessEqual(rows, {"S"})
        page.tier_box.setCurrentIndex(0)
        page.range_box.setCurrentIndex(1)  # 7 days
        self.assertGreater(page.table.rowCount(), 100)

    def test_settings_tab_builds_and_applies_at_once(self):
        holder = QWidget()
        layout = QVBoxLayout(holder)
        tu.build_settings_tab(layout, self.hub)
        boxes = holder.findChildren(QCheckBox)
        self.assertGreater(len(boxes), 20)
        self.assertGreater(len(holder.findChildren(QLineEdit)), 20)
        incursions = next(i for i, c in enumerate([c for c in td.CARDS if c.page == "live"]) if c.id == "incursions")
        self.assertFalse(self.hub.state.is_on("incursions"))
        self.assertIsNotNone(incursions)

    def test_tray_hides_instead_of_quitting_only_when_enabled(self):
        window = QWidget()
        quit_calls = []
        tray = tu.TrayController(window, self.hub, window.windowIcon(), lambda: quit_calls.append(1))
        tray.available = True
        self.assertTrue(tray.should_hide_on_close())
        self.hub.state.set_option("tray", False)
        self.assertFalse(tray.should_hide_on_close())


class DashboardTrackerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import inspect
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        cls.directory = tempfile.TemporaryDirectory(prefix="parallel-trackers-test-")
        cls.environment = patch.dict(os.environ, {"TTB_DATA_DIR": cls.directory.name})
        cls.environment.start()
        import qt_ui
        cls.ui = qt_ui
        store = SimpleNamespace(listings={}, items={}, load=lambda: None, listed_rows=lambda: [])
        callbacks = {n: Mock(return_value="x") for n in inspect.signature(qt_ui.Dashboard).parameters if n.startswith("on_")}
        with patch.object(qt_ui.config, "FIRST_LAUNCH", False):
            cls.window = qt_ui.Dashboard(store, **callbacks)

    @classmethod
    def tearDownClass(cls):
        cls.window.deleteLater()
        cls.environment.stop()
        cls.directory.cleanup()

    def test_sidebar_has_tracker_pages_and_links_work(self):
        window = self.window
        self.assertEqual(set(window.tracker_page_index), {"live", "daily", "weekly", "archimedea", "arbitration", "news"})
        window.tracker_hub.go_to_page("arbitration")
        self.assertEqual(window.tabs.currentIndex(), window.tracker_page_index["arbitration"])
        window.tracker_hub.go_to_baro()
        self.assertEqual(window.tabs.currentIndex(), window.trading_tab_index)
        self.assertIs(window.trading_page.tabs.currentWidget(), window.trading_page.baro)

    def test_renaming_a_page_renames_the_sidebar_button(self):
        window = self.window
        window.tracker_hub.state.set_page_title("daily", "Dailies")
        window.tracker_hub.card_changed()
        self.assertEqual(window.tabs.tabText(window.tracker_page_index["daily"]), "Dailies")
        window.tracker_hub.state.set_page_title("daily", "")
        window.tracker_hub.card_changed()
        self.assertEqual(window.tabs.tabText(window.tracker_page_index["daily"]), "Daily")

    def test_settings_dialog_gets_a_trackers_tab(self):
        dialog = self.ui.SettingsDialog(self.window)
        self.addCleanup(dialog.deleteLater)
        from PySide6.QtWidgets import QTabWidget
        tabs = dialog.findChild(QTabWidget, "SettingsTabs")
        names = [tabs.tabToolTip(i) for i in range(tabs.count())]
        self.assertIn("Trackers", names)

    def test_close_button_hides_to_tray_and_quit_really_closes(self):
        from PySide6.QtGui import QCloseEvent
        window = self.window
        window.tracker_tray.available = True
        window.tracker_hub.state.set_option("tray", True)
        window.show()
        event = QCloseEvent()
        window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertFalse(window.isVisible())
        window.tracker_hub.state.set_option("tray", False)
        window.show()
        event = QCloseEvent()
        window.closeEvent(event)
        self.assertTrue(event.isAccepted())


if __name__ == "__main__":
    unittest.main()
