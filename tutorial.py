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
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

import config


@dataclass(frozen=True)
class Step:
    title: str
    where: str          # where to find this in the app ("" for none)
    body: str           # simple HTML


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
            "Dashboard, top row: Login / Token (afterwards: Settings, Account tab)",
            "<p>Prices and listings need your warframe.market sign-in. Click <b>Login / Token</b>, "
            "then <b>Get token automatically</b>, and sign in to warframe.market in the window that "
            "opens. Parallel picks up the token for you. The token is stored only on this computer.</p>"
            "<p>Treat the token like a password and never share it. Once a token is saved the "
            "button disappears; if it stops working, enter a new one in <b>Settings</b>, "
            "<b>Account</b> tab.</p>",
        ),
        Step(
            "Reward screen prices",
            f"Hotkey {_hotkey('HOTKEY_REWARD_SCAN', 'ctrl+shift+r')}, or tick Void Fissure Scanning",
            "<p>On a Void Fissure reward screen, press the hotkey. Parallel reads the four rewards "
            "and shows a price tag above each one, with the best choice highlighted.</p>"
            "<p>Tick <b>Void Fissure Scanning</b> on the dashboard to do this automatically. Rewards "
            "you pick are added to a review queue (<b>Review rewards</b>); nothing is listed until "
            "you approve it.</p>",
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
            "My Items & Sets",
            "Left navigation rail: My Items & Sets",
            "<p>Everything Parallel knows you own, with what is listed and how close each set is "
            "to complete. A full progress bar in the accent colour means you can build the "
            "set now; rows one or two parts away are highlighted too.</p>"
            "<p>Use the filters and search, and <b>Refresh set data</b> if a set looks wrong. "
            "<b>Add set to Mastery List</b> sends the selected item's set to your list.</p>",
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
            "<p>Add an arcane and how many you have. Counts are treated as <b>max rank</b>, so "
            "combine them in game first.</p>"
            "<p><b>Check market</b> looks up max-rank prices from players who are in game. Then "
            "<b>List / update ticked</b> posts or changes your listings, after a confirmation.</p>",
        ),
        Step(
            "Settings: where things are",
            "Dashboard, top row: Settings",
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
            "screen reading), and enter your exact Warframe name in <b>Settings</b>, "
            "<b>Account</b> so the trade monitor can recognise your sales.</p>"
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
