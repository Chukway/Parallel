import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import config
import main
import qt_ui
import tutorial
from account_store import StoredItem
from set_catalog import SetCatalog
from settings_validation import sanitize_settings

_app = QApplication.instance() or QApplication([])

SET = {
    "name": "Velox Prime Set",
    "fetched": 9e12,
    "parts": [
        {"slug": "velox_prime_barrel", "name": "Velox Prime Barrel", "needed": 1},
        {"slug": "velox_prime_blueprint", "name": "Velox Prime Blueprint", "needed": 1},
        {"slug": "velox_prime_receiver", "name": "Velox Prime Receiver", "needed": 1},
        {"slug": "velox_prime_stock", "name": "Velox Prime Stock", "needed": 1},
    ],
}


def _fake_app(owned: dict[str, int]):
    catalog = SetCatalog(Path(tempfile.mkdtemp()) / "sets.json")
    catalog.sets["velox_prime_set"] = SET
    catalog.member_of = {p["slug"]: "velox_prime_set" for p in SET["parts"]}
    catalog.member_of["velox_prime_set"] = "velox_prime_set"
    items = {slug: StoredItem(slug, slug, qty) for slug, qty in owned.items()}
    return SimpleNamespace(_set_catalog=catalog, account_store=SimpleNamespace(items=items))


class OwnedInfoTests(unittest.TestCase):
    def test_counts_owned_parts_of_the_set(self):
        app = _fake_app({"velox_prime_barrel": 1, "velox_prime_receiver": 2})
        info = main.App._reward_owned_info(app, "velox_prime_blueprint")
        self.assertEqual((info["have"], info["total"]), (2, 4))
        this = [p for p in info["parts"] if p["this"]]
        self.assertEqual(this[0]["name"], "Blueprint")
        self.assertFalse(this[0]["owned"])

    def test_item_outside_a_set_has_no_info(self):
        app = _fake_app({})
        app._set_catalog.member_of["forma_blueprint"] = "-"
        self.assertIsNone(main.App._reward_owned_info(app, "forma_blueprint"))


class StyleSettingTests(unittest.TestCase):
    def test_only_known_styles_are_accepted(self):
        self.assertEqual(sanitize_settings({"reward_owned_style": "list"})["reward_owned_style"], "list")
        self.assertNotIn("reward_owned_style", sanitize_settings({"reward_owned_style": "bogus"}))

    def test_default_is_compact(self):
        self.assertEqual(config.REWARD_OWNED_STYLE, "cards")

    def test_tutorial_has_a_picker_step(self):
        self.assertTrue(any(step.choice for step in tutorial.build_steps()))


class TagTests(unittest.TestCase):
    OWNED = {
        "set_name": "Velox Prime Set", "have": 2, "total": 4,
        "parts": [{"name": "Barrel", "owned": True, "this": False},
                  {"name": "Blueprint", "owned": False, "this": True}],
    }

    def test_price_tag_shows_count_inline(self):
        tag = qt_ui.RewardPriceTag("Velox Prime Blueprint", 45, 100, 0, 0, 1000, owned=self.OWNED)
        texts = [label.text() for label in tag.findChildren(qt_ui.QLabel)]
        self.assertTrue(any("Own 2/4" in text for text in texts))
        tag.close()

    def test_tags_respect_a_narrow_slot(self):
        price = qt_ui.RewardPriceTag("Gyre Prime Chassis Blueprint", 5, 100, 0, 0, 1000, owned=self.OWNED, width=150)
        set_tag = qt_ui.RewardSetTag(self.OWNED, 0, 0, 1000, width=150)
        alts = qt_ui.RewardAlternateTag(
            [{"display_name": "Gyre Prime Neuroptics Blueprint", "price": 9, "match_score": 79, "scan_count": 0}],
            0, 0, 1000, width=150,
        )
        self.assertEqual(price.width(), 150)
        self.assertEqual(set_tag.width(), 150)
        self.assertEqual(alts.width(), 150)
        for tag in (price, set_tag, alts):
            tag.close()

    def test_set_tag_lists_parts(self):
        tag = qt_ui.RewardSetTag(self.OWNED, 0, 0, 1000)
        texts = [label.text() for label in tag.findChildren(qt_ui.QLabel)]
        self.assertIn("Velox Prime Set 2/4", texts)
        self.assertTrue(any("Blueprint (this)" in text for text in texts))
        tag.close()


class RankPanelTests(unittest.TestCase):
    def rows(self):
        part = lambda name, owned, this=False: {"name": name, "owned": owned, "this": this}
        return [
            {"display_name": "Gyre Prime Neuroptics Blueprint", "price": 12, "ducats": 45,
             "owned": {"set_name": "Gyre Prime Set", "have": 2, "total": 4,
                       "parts": [part("Chassis", True), part("Neuroptics", False, True), part("Systems", True), part("Blueprint", False)]}},
            {"display_name": "Kronen Prime Blade", "price": 30, "ducats": 15, "vaulted": True,
             "trend": {"direction": "down", "arrow": "▼"}, "owned": None},
            {"display_name": "Forma Blueprint", "price": None, "ducats": None},
        ]

    def test_sorting_by_plat_or_ducats(self):
        import qt_ui
        rows = self.rows()
        by_plat = sorted(rows, key=lambda r: qt_ui.RewardRankPanel._sort_key(r, "plat"))
        by_ducats = sorted(rows, key=lambda r: qt_ui.RewardRankPanel._sort_key(r, "ducats"))
        self.assertEqual([r["display_name"] for r in by_plat][:2], ["Kronen Prime Blade", "Gyre Prime Neuroptics Blueprint"])
        self.assertEqual(by_ducats[0]["display_name"], "Gyre Prime Neuroptics Blueprint")
        self.assertEqual(by_plat[-1]["display_name"], "Forma Blueprint")      # unpriced goes last

    def test_row_shows_what_the_pick_adds_to_the_set(self):
        import qt_ui
        html = qt_ui.RewardRankPanel.row_html(1, self.rows()[0], best=True)
        self.assertIn("2/4 → 3/4", html)
        self.assertIn("<b>+</b>", html)
        self.assertIn("45d", html)
        done = dict(self.rows()[0])
        done["owned"] = dict(done["owned"], have=3)
        self.assertIn("set done", qt_ui.RewardRankPanel.row_html(1, done, best=True))

    def test_duplicate_part_says_have_it_and_vaulted_marker_shows(self):
        import qt_ui
        row = self.rows()[0]
        row["owned"]["parts"][1]["owned"] = True
        self.assertIn("(have it)", qt_ui.RewardRankPanel.row_html(1, row, best=False))
        self.assertIn(">V<", qt_ui.RewardRankPanel.row_html(2, self.rows()[1], best=False))

    def test_panel_updates_in_place_without_new_windows(self):
        import qt_ui
        panel = qt_ui.RewardRankPanel()
        panel.update_rows(self.rows(), 5000)
        labels = list(panel._labels)
        panel.update_rows(self.rows()[:2], 5000)
        self.assertEqual(panel._labels, labels[:len(panel._labels)])        # same label objects reused
        self.assertEqual(sum(1 for l in panel._labels if not l.isHidden()), 2)
        panel.close()

class CardTagsTests(unittest.TestCase):
    def rows(self):
        part = lambda n, o, t=False: {"name": n, "owned": o, "this": t}
        return [
            {"display_name": "A Prime Blueprint", "price": 7, "ducats": 45, "card_rect": (0, 0, 240, 240),
             "owned": {"have": 0, "total": 4, "parts": [part("A", False, True), part("B", False), part("C", False), part("D", False)]}},
            {"display_name": "B Prime Chassis", "price": 3, "ducats": 45, "card_rect": (250, 0, 240, 240),
             "owned": {"have": 1, "total": 4, "parts": [part("A", True, True), part("B", False), part("C", False), part("D", False)]}},
            {"display_name": "Forma Blueprint", "price": None, "ducats": None, "card_rect": (500, 0, 240, 240), "owned": None},
        ]

    def test_recommendation_numbers_and_colours(self):
        import qt_ui
        rows = self.rows()
        ranks = qt_ui._reward_ranks(rows)
        self.assertEqual([ranks.get(i) for i in range(3)], [1, 2, None])    # unpriced (Forma) gets no number
        best_top = qt_ui.RewardCardTags.top_html(rows[0], True)
        other_top = qt_ui.RewardCardTags.top_html(rows[1], False)
        self.assertIn(qt_ui.C_GREEN, best_top)
        self.assertIn("45d", best_top)
        self.assertNotIn(qt_ui.C_GREEN, other_top.split("45d")[0])
        bottom = qt_ui.RewardCardTags.bottom_html(rows[0], 1)
        self.assertIn("A Prime Blueprint", bottom)
        self.assertIn("0/4 → 1/4", bottom)
        self.assertIn(">1<", bottom)

    def test_ranking_follows_the_setting(self):
        import qt_ui
        rows = self.rows()
        rows[1]["ducats"] = 100
        old = config.REWARD_RANK_BY
        try:
            config.REWARD_RANK_BY = "ducats"
            self.assertEqual(qt_ui._reward_ranks(rows)[1], 1)
        finally:
            config.REWARD_RANK_BY = old

    def test_tags_are_reused_and_click_through(self):
        import qt_ui
        tags = qt_ui.RewardCardTags()
        tags.update(self.rows(), 5000)
        first = list(tags.pairs)
        self.assertEqual(len(first), 3)
        self.assertTrue(first[0][0].testAttribute(qt_ui.Qt.WA_TransparentForMouseEvents))
        tags.update(self.rows(), 5000)
        self.assertEqual(tags.pairs, first)                 # same windows, no flicker
        tags.close()

if __name__ == "__main__":
    unittest.main()
