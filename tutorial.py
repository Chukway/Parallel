"""First-run tour that explains what Parallel does and where the settings live.

The tour is a small dialog with Back / Next and a Skip button on every page.
Whether it has been seen is remembered in ``tutorial_done.json`` in the data
folder, so it appears once and can be replayed from the dashboard or Settings.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QRadioButton, QVBoxLayout, QWidget,
)

import config


@dataclass(frozen=True)
class Step:
    title: str
    where: str          # where to find this in the app ("" for none)
    body: str           # simple HTML
    choice: bool = False  # show the reward-screen ownership style picker


OWNED_STYLES = (
    ("cards", "Tags on each card: plat and ducats above, number and set below (recommended)"),
    ("panel", "Ranked list: one small box in the top-left corner (recommended)"),
    ("inline", "Compact: show 'Own 2/4' beside the price"),
    ("list", "Detailed: list the set's parts under each reward"),
)


def _apply_owned_style(style: str):
    """Remember the picked reward-screen style (settings file and running app)."""
    config.REWARD_OWNED_STYLE = style if style in ("cards", "panel", "inline", "list") else "cards"
    try:
        import qt_ui   # imported late: qt_ui imports this module
        qt_ui._save_ui_settings()
    except Exception as e:
        print(f"[Tutorial] Could not save the reward style: {e}")


def _hotkey(name: str, fallback: str) -> str:
    return str(getattr(config, name, fallback)).upper().replace("+", " + ")


def build_steps() -> list[Step]:
    name = config.APP_NAME
    return [
        Step(
            f"Welcome to {name}",
            "",
            f"<p>{name} is a trading companion for Warframe. It reads your screen, looks up "
            "prices on warframe.market, and helps you list items, track sets and plan mastery.</p>"
            "<p>It never changes your market listings on its own. Every listing, update or delete "
            "is shown to you first and only happens when you confirm it.</p>"
            "<p>This short tour shows what each part does. You can <b>skip it</b> at any time and "
            "replay it later from Settings.</p>",
        ),
        Step(
            "Connect your market account",
            "Left side, bottom: Login / Token (afterwards: Settings, Account tab)",
            "<p>Prices and listings need your warframe.market sign-in. Click <b>Login / Token</b>, "
            "then <b>Get token automatically</b>, and sign in to warframe.market in the window that "
            "opens. Parallel picks up the token for you. The token is stored only on this computer.</p>"
            "<p>Treat the token like a password and never share it. Once a token is saved the "
            "button disappears; if it stops working, enter a new one in <b>Settings</b>, "
            "<b>Account</b> tab.</p>",
        ),
        Step(
            "Reward screen prices",
            f"Hotkey {_hotkey('HOTKEY_REWARD_SCAN', 'ctrl+shift+r')}, or tick Void Fissure Scanning (top of the window)",
            "<p>On a Void Fissure reward screen, press the hotkey. Parallel reads the four rewards "
            "and shows a price tag above each one, with the best choice highlighted.</p>"
            "<p>Tick <b>Void Fissure Scanning</b> at the top of the window to do this automatically. Rewards "
            "you pick are added to a review queue (<b>Review rewards</b>); nothing is listed until "
            "you approve it.</p>",
        ),
        Step(
            "Pick your reward screen style",
            "Changeable any time in Settings, Alerts tab",
            "<p>Choose how the reward screen overlay looks:</p>"
            "<p><b>Tags on each card</b>: platinum and ducats above every card (the best one in green) and, below it, a number 1-4, the name and your set progress.</p>"
            "<p><b>Ranked list</b>: one small box in the top-left corner, best reward first, with "
            "platinum, ducats, price direction, vaulted marker and what the pick does to your set "
            "(for example 2/4 to 3/4). It stays clear of the cards.</p>"
            "<p><b>Compact</b>: a price tag above each card with <span style='color:#d9b24a'>45p</span> "
            "| Own 2/4.</p>"
            "<p><b>Detailed</b>: the price tag plus a box under each card listing the set's parts.</p>",
            choice=True,
        ),
        Step(
            "Scan your inventory",
            f"Hotkeys {_hotkey('HOTKEY_INVENTORY_SCAN', 'ctrl+shift+i')} and "
            f"{_hotkey('HOTKEY_INVENTORY_BOTTOM_SCAN', 'ctrl+shift+b')}",
            "<p>Open Warframe's Inventory sell screen and press the first hotkey to scan the "
            "visible 6x3 grid. Scroll to the end and use the second hotkey for the last row.</p>"
            "<p>A review window opens. Rows start unticked: tick what you want, check the price "
            "and the action (<b>UPDATE</b>, <b>SKIP</b> or <b>DELETE</b>), then apply. Low-value "
            "items can be held back for ducats.</p>"
            "<p>The <b>Selling Preset</b> menu sets the price rule, for example Most Common, "
            "Quicksell or Lowest -1p.</p>",
        ),
        Step(
            "Item List",
            "Left navigation rail: Item List",
            "<p>Everything Parallel knows you own, with what is listed and how close each set is "
            "to complete. A full progress bar means you can build the set now.</p>"
            "<p>Top row: filter, search, <b>Refresh set data</b> and <b>Select all</b> (it turns into "
            "<b>Deselect all</b>). Second row, for the selected items:</p>"
            "<ul>"
            "<li><b>Sell selected on warframe.market</b>: opens the review window; nothing is listed until you confirm.</li>"
            "<li><b>Add set to Mastery List</b>: sends the item's set to your Mastery List.</li>"
            "<li><b>Combine set</b>: lists a full set and lowers the listings of its parts so you do not sell the same items twice. You pick the price and see every change before it is sent.</li>"
            "<li><b>Set vs parts</b>: one line per selected set showing whether the full set or its parts sell for more. Double-click a set for each part's price.</li>"
            "<li><b>Add to watchlist</b> and <b>Watch still-needed parts</b>: send items, or only the parts you are missing, to the Watchlist.</li>"
            "</ul>"
            "<p>The bottom row adds, edits or removes items by hand. When you remove an item (here, or by deleting a listing in the Trading Post) Parallel asks whether it was sold and, if so, records the sale with the price you enter. If you sold only some of a stack, say how many; the rest stays.</p>",
        ),
        Step(
            "Mastery List",
            "Left navigation rail: Mastery List",
            "<p>Type a set name to see every part you need, how many you own and what is missing. "
            "Sets stay on your list between sessions.</p>"
            "<p><b>Copy shopping list</b> puts all missing parts on your clipboard, ready to search "
            "on warframe.market.</p>",
        ),
        Step(
            "Arcanes",
            "Left navigation rail: Arcanes",
            "<p>Add an arcane and how many copies you have. Each row is an arcane at one rank.</p>"
            f"<p>Or open Warframe's ARCANES screen and press "
            f"{_hotkey('HOTKEY_ARCANE_SCAN', 'ctrl+shift+a')}: Parallel reads every tile's name and rank "
            "(the filled diamonds), counts identical ones and asks you to confirm before saving. "
            "Scroll and press it again for more; a count is only ever raised to the larger of your "
            "stock and what is on screen, so overlapping screens are not counted twice.</p>"
            "<p><b>Check market</b> looks up the price of a single arcane from players who are in game. "
            "Then <b>List / update ticked</b> posts or changes your listings, after a confirmation.</p>"
            "<p>Prices and listings use the rank of each row, so a max rank copy is compared with other "
            "max rank sellers.</p>",
        ),
        Step(
            "Trading Tools",
            "Left navigation rail: Trading Tools",
            "<ul>"
            "<li><b>Watchlist</b>: alerts when something you want to buy drops to your price, or something you hold "
            "reaches yours. <b>Update prices</b> shows the lowest and average sell price, the top buy order and a "
            "suggested buy price. <b>Post buy listing</b> creates a buy order on warframe.market after a review window.</li>"
            "<li><b>Repricing</b>: finds listings other players are undercutting and suggests a new price. "
            "<b>Select all</b>, adjust, then Apply and confirm.</li>"
            "<li><b>Sales</b>: platinum today, this week and overall. <b>Add sale</b> records one by hand "
            "(for example a trade the monitor missed); you can also edit, delete and export to CSV.</li>"
            "<li><b>Baro Ki'Teer</b>: his stock with market prices and the best ducats-to-platinum deals.</li>"
            "<li><b>Rewards</b>: every reward screen Parallel read. If it missed your pick, select the reward and add it to your Item List.</li>"
            "</ul>"
            "<p>On the reward screen a gold <b>V</b> marks vaulted parts and an arrow shows whether the price "
            "is rising or falling.</p>",
        ),
        Step(
            "Settings: where things are",
            "Left side, bottom: Settings",
            "<ul>"
            "<li><b>UI Appearance</b>: theme, accent colour, fonts and colours.</li>"
            "<li><b>Account</b>: your in-game name and market token.</li>"
            "<li><b>Pricing</b>: the lowest price Parallel will ever suggest.</li>"
            "<li><b>OCR</b>: the screen-reading engine and its fallback.</li>"
            "<li><b>Alerts</b>: alert sounds and pop-up notifications.</li>"
            "<li><b>Visuals</b>: the character picture shown beside pop-ups.</li>"
            "<li><b>Ducats</b>: hold back low-value items instead of selling them.</li>"
            "<li><b>Hotkeys</b>: change any shortcut.</li>"
            "<li><b>Updates</b>: how often Parallel checks for a new version.</li>"
            "</ul>",
        ),
        Step(
            "You're ready",
            "",
            "<p>That is the whole tour. A good first run: connect your account, open Warframe's "
            "inventory, press the inventory hotkey, and review the results.</p>"
            "<p>Two tips for good scans: keep <b>HDR off</b> in Windows while scanning (it hurts "
            "screen reading), and give Parallel your exact Warframe name so the trade monitor can "
            "recognise your sales. It asks for it at start-up; change it any time in "
            "<b>Settings</b>, <b>Account</b>.</p>"
            "<p>Replay this tour any time from <b>Settings</b>, <b>Replay tutorial</b>.</p>",
        ),
    ]


def _marker_path() -> Path:
    return Path(config.USER_DATA_DIR) / "tutorial_done.json"


def tutorial_seen() -> bool:
    try:
        return bool(json.loads(_marker_path().read_text(encoding="utf-8")).get("done"))
    except (OSError, ValueError, AttributeError):
        return False


def mark_tutorial_seen(done: bool = True):
    try:
        path = _marker_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"done": bool(done)}), encoding="utf-8")
    except OSError:
        pass


class TutorialDialog(QDialog):
    """Paged tour. Skip is always available and counts as seen."""

    def __init__(self, parent: QWidget | None = None, steps: list[Step] | None = None):
        super().__init__(parent)
        self.steps = steps or build_steps()
        self.index = 0
        self.setWindowTitle(f"{config.APP_NAME} tour")
        self.setModal(True)
        self.setMinimumSize(560, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)

        self.progress = QLabel("")
        self.progress.setObjectName("Muted")
        layout.addWidget(self.progress)
        self.title = QLabel("")
        self.title.setObjectName("Title")
        layout.addWidget(self.title)
        self.where = QLabel("")
        self.where.setObjectName("Muted")
        self.where.setWordWrap(True)
        layout.addWidget(self.where)
        self.body = QLabel("")
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.RichText)
        self.body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        layout.addWidget(self.body, 1)

        self.style_buttons: dict[str, QRadioButton] = {}
        self.style_box = QWidget()
        style_layout = QVBoxLayout(self.style_box)
        style_layout.setContentsMargins(0, 0, 0, 0)
        current = str(getattr(config, "REWARD_OWNED_STYLE", "cards"))
        for key, label in OWNED_STYLES:
            button = QRadioButton(label)
            button.setChecked(key == current)
            button.toggled.connect(lambda checked, k=key: checked and _apply_owned_style(k))
            self.style_buttons[key] = button
            style_layout.addWidget(button)
        layout.addWidget(self.style_box)

        self.dont_show = QCheckBox("Don't show this tour again")
        self.dont_show.setChecked(True)
        layout.addWidget(self.dont_show)

        buttons = QHBoxLayout()
        self.skip_button = QPushButton("Skip tour")
        self.skip_button.setToolTip("Close the tour. You can replay it from Settings.")
        self.skip_button.clicked.connect(self.skip)
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(self.back)
        self.next_button = QPushButton("Next")
        self.next_button.setDefault(True)
        self.next_button.clicked.connect(self.next)
        buttons.addWidget(self.skip_button)
        buttons.addStretch()
        buttons.addWidget(self.back_button)
        buttons.addWidget(self.next_button)
        layout.addLayout(buttons)

        self._show_step()

    def _show_step(self):
        step = self.steps[self.index]
        last = self.index == len(self.steps) - 1
        self.progress.setText(f"Step {self.index + 1} of {len(self.steps)}")
        self.title.setText(step.title)
        self.where.setText(f"Where: {step.where}" if step.where else "")
        self.where.setVisible(bool(step.where))
        self.body.setText(step.body)
        self.style_box.setVisible(step.choice)
        self.back_button.setEnabled(self.index > 0)
        self.next_button.setText("Done" if last else "Next")
        self.skip_button.setVisible(not last)

    def next(self):
        if self.index >= len(self.steps) - 1:
            self._finish()
            return
        self.index += 1
        self._show_step()

    def back(self):
        if self.index > 0:
            self.index -= 1
            self._show_step()

    def skip(self):
        self._finish()

    def _finish(self):
        if self.dont_show.isChecked():
            mark_tutorial_seen(True)
        self.accept()

    def reject(self):
        # Closing the window with Esc or the X behaves like Skip.
        self._finish()


def show_tutorial_if_needed(parent: QWidget | None = None) -> bool:
    """Show the tour once. Returns True if it was shown."""
    if tutorial_seen():
        return False
    TutorialDialog(parent).exec()
    return True


def replay_tutorial(parent: QWidget | None = None):
    TutorialDialog(parent).exec()
