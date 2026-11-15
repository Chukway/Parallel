import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tracker_data as td

UTC = timezone.utc


def at(*args):
    return datetime(*args, tzinfo=UTC)


class CycleTests(unittest.TestCase):
    def test_roll_forward_keeps_state_while_running(self):
        state, expiry = td.roll_cycle(td.CYCLE_PATTERNS["cetus"], "day", at(2026, 1, 1, 12), at(2026, 1, 1, 11))
        self.assertEqual((state, expiry), ("day", at(2026, 1, 1, 12)))

    def test_roll_forward_across_several_cycles(self):
        pattern = td.CYCLE_PATTERNS["cetus"]  # 100 min day, 50 min night
        state, expiry = td.roll_cycle(pattern, "day", at(2026, 1, 1, 12), at(2026, 1, 1, 12, 10))
        self.assertEqual(state, "night")
        self.assertEqual(expiry, at(2026, 1, 1, 12, 50))
        # a whole number of cycles later it lands on the same state again
        state, expiry = td.roll_cycle(pattern, "day", at(2026, 1, 1, 12), at(2026, 1, 1, 12) + timedelta(minutes=150 * 40 + 10))
        self.assertEqual(state, "night")

    def test_unknown_state_is_left_alone(self):
        self.assertEqual(td.roll_cycle(td.CYCLE_PATTERNS["cetus"], "dusk", at(2026, 1, 1), at(2027, 1, 1))[0], "dusk")

    def test_duviri_moods_rotate_in_order(self):
        state, _ = td.roll_cycle(td.CYCLE_PATTERNS["duviri"], "fear", at(2026, 1, 1, 2), at(2026, 1, 1, 2, 5))
        self.assertEqual(state, "joy")


class ResetTests(unittest.TestCase):
    def test_period_keys(self):
        wednesday = at(2026, 11, 11, 15)
        self.assertEqual(td.period_key("daily", wednesday), "2026-11-11")
        self.assertEqual(td.period_key("weekly", wednesday), "2026-11-09")  # that Monday
        self.assertEqual(td.period_key("weekly", at(2026, 11, 9, 0, 0, 1)), "2026-11-09")
        self.assertEqual(td.period_key("weekly", at(2026, 11, 8, 23, 59)), "2026-11-02")

    def test_next_reset(self):
        self.assertEqual(td.next_reset("daily", at(2026, 11, 11, 15)), at(2026, 11, 12))
        self.assertEqual(td.next_reset("weekly", at(2026, 11, 11, 15)), at(2026, 11, 16))
        self.assertEqual(td.next_reset("weekly", at(2026, 11, 16, 0)), at(2026, 11, 23))

    def test_span_text(self):
        self.assertEqual(td.fmt_span(45), "45s")
        self.assertEqual(td.fmt_span(410), "6m 50s")
        self.assertEqual(td.fmt_span(2520), "42m")
        self.assertEqual(td.fmt_span(8100), "2h 15m")
        self.assertEqual(td.fmt_span(190000), "2d 4h")


class StateTests(unittest.TestCase):
    def test_ticks_clear_at_the_reset_and_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.json"
            state = td.TrackerState(path)
            state.set_tick("archon", "weekly", True, at(2026, 11, 11))
            self.assertTrue(state.is_ticked("archon", "weekly", at(2026, 11, 15, 23)))
            self.assertFalse(state.is_ticked("archon", "weekly", at(2026, 11, 16, 0, 1)))
            self.assertTrue(td.TrackerState(path).is_ticked("archon", "weekly", at(2026, 11, 12)))
            state.set_tick("archon", "weekly", False, at(2026, 11, 12))
            self.assertFalse(state.is_ticked("archon", "weekly", at(2026, 11, 12)))

    def test_prune_drops_old_periods(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = td.TrackerState(Path(tmp) / "t.json")
            state.set_tick("sortie", "daily", True, at(2026, 11, 1))
            state.set_tick("archon", "weekly", True, at(2026, 11, 11))
            state.prune_ticks(at(2026, 11, 12))
            self.assertEqual(list(state.ticks), ["weekly:archon"])

    def test_defaults_and_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.json"
            state = td.TrackerState(path)
            for off in ("bounties", "darvo", "alerts", "events", "invasions", "storms", "incursions"):
                self.assertFalse(state.is_on(off), off)
            for on in ("cycles", "arbitration", "fissures", "sortie", "eta", "eda"):
                self.assertTrue(state.is_on(on), on)
            state.set_on("bounties", True)
            state.set_title("cycles", "Open worlds")
            state.set_page_title("live", "Right now")
            again = td.TrackerState(path)
            self.assertTrue(again.is_on("bounties"))
            self.assertEqual(again.title("cycles"), "Open worlds")
            self.assertEqual(again.page_title("live"), "Right now")
            again.set_title("cycles", "")
            self.assertEqual(again.title("cycles"), "World cycles")

    def test_broken_file_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.json"
            path.write_text("[1, 2", encoding="utf-8")
            self.assertTrue(td.TrackerState(path).is_on("cycles"))


class ArbitrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        start = int(at(2026, 11, 14, 20).timestamp())
        (root / "s.txt").write_text(
            "\n".join(f"{start + 3600 * i},{node}" for i, node in enumerate(["SolNode1", "SolNode2", "SolNode3", "SolNode4", "SolNode5"])),
            encoding="ascii",
        )
        (root / "n.json").write_text(
            '{"SolNode1":{"value":"Hydron (Sedna)","enemy":"Grineer","type":"Defense"},'
            '"SolNode2":{"value":"Odin (Mercury)","enemy":"Infested","type":"Survival"},'
            '"SolNode3":{"value":"Helene (Saturn)","enemy":"Grineer","type":"Excavation"},'
            '"SolNode4":{"value":"Lith (Earth)","enemy":"Corpus","type":"Defense"},'
            '"SolNode5":{"value":"Cinxia (Ceres)","enemy":"Grineer","type":"Interception"}}',
            encoding="utf-8",
        )
        (root / "t.json").write_text('{"tiers":{"SolNode1":"S","SolNode2":"B","SolNode3":"A"}}', encoding="utf-8")
        self.schedule = td.ArbitrationSchedule(root / "s.txt", root / "n.json", root / "t.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_current_and_three_upcoming(self):
        now = at(2026, 11, 14, 20, 30)
        current = self.schedule.current(now)
        self.assertEqual((current.name, current.tier, current.mission, current.faction), ("Hydron (Sedna)", "S", "Defense", "Grineer"))
        self.assertEqual(current.end, at(2026, 11, 14, 21))
        upcoming = self.schedule.upcoming(now, 3)
        self.assertEqual([a.name for a in upcoming], ["Odin (Mercury)", "Helene (Saturn)", "Lith (Earth)"])
        self.assertEqual(upcoming[2].tier, "F")  # not in the tier list

    def test_outside_the_schedule(self):
        self.assertIsNone(self.schedule.current(at(2026, 11, 14, 19)))
        self.assertIsNone(self.schedule.current(at(2026, 11, 15, 2)))
        self.assertEqual(self.schedule.upcoming(at(2026, 11, 15, 0, 30), 3), [])

    def test_window_filters(self):
        now = at(2026, 11, 14, 20, 15)
        self.assertEqual(len(self.schedule.window(now, 24)), 5)
        self.assertEqual([a.mission for a in self.schedule.window(now, 24, missions=["defense"])], ["Defense", "Defense"])
        self.assertEqual([a.tier for a in self.schedule.window(now, 24, tiers=["S", "A"])], ["S", "A"])
        self.assertEqual(len(self.schedule.window(now, 24, factions=["Grineer"])), 3)
        self.assertEqual(len(self.schedule.window(now, 2)), 3)

    def test_bundled_schedule_is_usable(self):
        real = td.ArbitrationSchedule()
        self.assertGreater(len(real), 40000)
        entry = real.current(at(2026, 11, 14, 12))
        self.assertIsNotNone(entry)
        self.assertTrue(entry.mission and entry.name)
        self.assertIn("Defense", real.mission_types())


class ViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = td.WorldState(Path(self.tmp.name) / "ws.json")
        self.ws.data = {
            "cetusCycle": {"state": "night", "expiry": "2026-11-14T21:38:00.000Z"},
            "vallisCycle": {"isWarm": False, "expiry": "2026-11-14T21:33:28.000Z"},
            "fissures": [
                {"tier": "Lith", "tierNum": 1, "missionType": "Survival", "node": "Hydron (Sedna)", "expiry": "2026-11-14T22:00:00.000Z", "isHard": False, "isStorm": False},
                {"tier": "Axi", "tierNum": 3, "missionType": "Defense", "node": "Ur (Uranus)", "expiry": "2026-11-14T22:30:00.000Z", "isHard": True, "isStorm": False},
                {"tier": "Neo", "tierNum": 2, "missionType": "Skirmish", "node": "Nu-ari (Void)", "expiry": "2026-11-14T22:30:00.000Z", "isHard": False, "isStorm": True},
                {"tier": "Meso", "tierNum": 2, "missionType": "Capture", "node": "Lua", "expiry": "2026-11-14T19:00:00.000Z", "isHard": False, "isStorm": False},
            ],
            "archimedeas": [
                {"typeKey": "C T_ L A B", "expiry": "2026-11-16T00:00:00.000Z",
                 "missions": [{"faction": "Man in the Wall", "missionType": "Defense",
                               "deviation": {"name": "Eroding Senses", "description": "x"},
                               "risks": [{"name": "Fortified", "description": "y", "isHard": True}, {"name": "Explosive", "description": "z", "isHard": False}]}],
                 "personalModifiers": [{"name": "Gear Embargo", "description": "No gear."}]},
                {"typeKey": "C T_ H E X", "expiry": "2026-11-16T00:00:00.000Z", "missions": [], "personalModifiers": []},
            ],
            "duviriCycle": {"state": "joy", "expiry": "2026-11-14T22:00:00.000Z",
                            "choices": [{"category": "normal", "choices": ["Nidus"]}, {"category": "hard", "choices": ["Boar"]}]},
            "news": [{"message": "Old", "date": "2026-11-01T00:00:00.000Z"}, {"message": "New", "date": "2026-11-13T00:00:00.000Z"},
                     {"message": "Placeholder", "date": "1970-01-01T00:00:00.000Z"}],
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_fissures_split_and_drop_expired(self):
        groups = td.fissures_view(self.ws, at(2026, 11, 14, 20))
        self.assertEqual([r["node"] for r in groups["normal"]], ["Hydron (Sedna)"])
        self.assertEqual([r["node"] for r in groups["steel"]], ["Ur (Uranus)"])
        self.assertEqual([r["node"] for r in groups["storm"]], ["Nu-ari (Void)"])

    def test_cycles_roll_past_their_expiry_offline(self):
        rows = {r["key"]: r for r in td.cycles_view(self.ws, at(2026, 11, 14, 21, 40))}
        self.assertEqual(rows["cetus"]["state"], "day")  # the night ended at 21:38
        self.assertEqual(rows["vallis"]["state"], "warm")
        self.assertEqual(rows["cetus"]["next"], "night")

    def test_archimedea_maps_deep_and_temporal(self):
        view = td.archimedea_view(self.ws)
        self.assertEqual(view["eda"]["name"], "Deep Archimedea")
        self.assertEqual(view["eta"]["name"], "Temporal Archimedea")
        risks = view["eda"]["missions"][0]["risks"]
        self.assertEqual([(r["name"], r["elite"]) for r in risks], [("Fortified", True), ("Explosive", False)])
        self.assertEqual(view["eda"]["modifiers"][0]["name"], "Gear Embargo")

    def test_circuit_and_news(self):
        self.assertEqual(td.circuit_view(self.ws), {"warframes": ["Nidus"], "incarnon": ["Boar"]})
        self.assertEqual([n["message"] for n in td.news_view(self.ws)], ["New", "Old"])

    def test_missing_data_gives_empty_views(self):
        empty = td.WorldState(Path(self.tmp.name) / "none.json")
        now = at(2026, 11, 14)
        self.assertEqual(td.cycles_view(empty, now), [])
        self.assertEqual(td.fissures_view(empty, now), {"normal": [], "steel": [], "storm": []})
        self.assertIsNone(td.sortie_view(empty, now))
        self.assertEqual(td.archimedea_view(empty), {})

    def test_cache_round_trip(self):
        self.ws.fetched = 123.0
        path = self.ws.path
        path.write_text('{"fetched": 123.0, "data": {"cetusCycle": {}}}', encoding="utf-8")
        again = td.WorldState(path)
        self.assertEqual(again.fetched, 123.0)
        self.assertIn("cetusCycle", again.data)


class NotificationTests(unittest.TestCase):
    def test_arbitration_fires_once_for_good_tiers_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            start = int(at(2026, 11, 14, 20).timestamp())
            (root / "s.txt").write_text(f"{start},SolNode1\n{start + 3600},SolNode2\n", encoding="ascii")
            (root / "n.json").write_text('{"SolNode1":{"value":"Hydron (Sedna)","enemy":"Grineer","type":"Defense"},"SolNode2":{"value":"Odin","enemy":"Infested","type":"Survival"}}', encoding="utf-8")
            (root / "t.json").write_text('{"tiers":{"SolNode1":"S","SolNode2":"C"}}', encoding="utf-8")
            schedule = td.ArbitrationSchedule(root / "s.txt", root / "n.json", root / "t.json")
            state = td.TrackerState(root / "state.json")
            ws = td.WorldState(root / "ws.json")
            first = td.due_notifications(state, schedule, ws, at(2026, 11, 14, 20, 2))
            self.assertEqual(len(first), 1)
            self.assertIn("Hydron", first[0][1])
            self.assertEqual(td.due_notifications(state, schedule, ws, at(2026, 11, 14, 20, 30)), [])
            self.assertEqual(td.due_notifications(state, schedule, ws, at(2026, 11, 14, 21, 1)), [])  # tier C
            state.set_option("notify_arbitration", False)
            state.notified.clear()
            self.assertEqual(td.due_notifications(state, schedule, ws, at(2026, 11, 14, 20, 2)), [])

    def test_no_pop_up_for_one_that_started_long_ago(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            start = int(at(2026, 11, 14, 20).timestamp())
            (root / "s.txt").write_text(f"{start},SolNode1\n", encoding="ascii")
            (root / "n.json").write_text('{"SolNode1":{"value":"Hydron","enemy":"Grineer","type":"Defense"}}', encoding="utf-8")
            (root / "t.json").write_text('{"tiers":{"SolNode1":"S"}}', encoding="utf-8")
            schedule = td.ArbitrationSchedule(root / "s.txt", root / "n.json", root / "t.json")
            state = td.TrackerState(root / "state.json")
            self.assertEqual(td.due_notifications(state, schedule, td.WorldState(root / "ws.json"), at(2026, 11, 14, 20, 45)), [])

    def test_weekly_reminder_fires_in_the_last_hour(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            schedule = td.ArbitrationSchedule(root / "x", root / "y", root / "z")
            state = td.TrackerState(root / "state.json")
            state.set_option("notify_arbitration", False)
            state.set_option("notify_weekly", True)
            ws = td.WorldState(root / "ws.json")
            self.assertEqual(td.due_notifications(state, schedule, ws, at(2026, 11, 15, 22)), [])
            self.assertEqual(len(td.due_notifications(state, schedule, ws, at(2026, 11, 15, 23, 30))), 1)
            self.assertEqual(td.due_notifications(state, schedule, ws, at(2026, 11, 15, 23, 40)), [])


if __name__ == "__main__":
    unittest.main()
