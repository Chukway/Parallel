"""Asks for the player's Warframe name at start-up when it has not been set yet.

The name is used by the trade monitor and the reward scanner to recognise the
player's own rows. It can be changed any time in Settings, Account tab.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

import config


def _marker_path() -> Path:
    return Path(config.USER_DATA_DIR) / "name_prompt_off.json"


def prompt_disabled() -> bool:
    try:
        return bool(json.loads(_marker_path().read_text(encoding="utf-8")).get("off"))
    except (OSError, ValueError, AttributeError):
        return False


def disable_prompt():
    try:
        _marker_path().write_text(json.dumps({"off": True}), encoding="utf-8")
    except OSError:
        pass


def needs_name() -> bool:
    return not str(getattr(config, "WFM_INGAME_NAME", "") or "").strip() and not prompt_disabled()


class NamePromptDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("What is your Warframe name?")
        self.setModal(True)
        self.setMinimumWidth(460)
        self.name: str = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)
        title = QLabel("What is your Warframe name?")
        title.setObjectName("Title")
        layout.addWidget(title)
        body = QLabel(
            "Type your exact in-game name. Parallel uses it to recognise you on the trade window "
            "and the reward screen, so your own sales are counted correctly.")
        body.setWordWrap(True)
        layout.addWidget(body)
        self.field = QLineEdit()
        self.field.setPlaceholderText("Your exact in-game name")
        self.field.setMaxLength(64)
        self.field.textChanged.connect(self._sync)
        self.field.returnPressed.connect(self._save)
        layout.addWidget(self.field)
        where = QLabel("You can change it any time in <b>Settings</b>, then the <b>Account</b> tab.")
        where.setObjectName("Muted")
        where.setWordWrap(True)
        layout.addWidget(where)
        self.never = QCheckBox("Don't ask me again")
        layout.addWidget(self.never)
        row = QHBoxLayout()
        self.later = QPushButton("Later")
        self.later.clicked.connect(self._later)
        self.save = QPushButton("Save name")
        self.save.setDefault(True)
        self.save.setEnabled(False)
        self.save.clicked.connect(self._save)
        row.addWidget(self.later)
        row.addStretch()
        row.addWidget(self.save)
        layout.addLayout(row)

    def _sync(self, text: str):
        self.save.setEnabled(bool(text.strip()))

    def _save(self):
        name = self.field.text().strip()
        if not name:
            return
        self.name = name
        self.accept()

    def _later(self):
        if self.never.isChecked():
            disable_prompt()
        self.reject()


def ask_for_name_if_needed(parent: QWidget | None, save_name) -> bool:
    """Show the prompt once per start-up while no name is set. ``save_name(name)``
    stores it. Returns True when a name was saved."""
    if not needs_name():
        return False
    dialog = NamePromptDialog(parent)
    if dialog.exec() == QDialog.DialogCode.Accepted and dialog.name:
        save_name(dialog.name)
        return True
    return False
