"""A short history of Void Fissure reward screens, saved to ``reward_history.json``.

Each entry is one reward screen: the rewards that were read (with prices) and which
one Parallel saw you pick. When a pick can't be confirmed, the screen is numbered
"Failure 1", "Failure 2" and so on, and the Rewards tab lets you add the reward you
took to your Item List by hand.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path

import config

MAX_SCREENS = 100


class RewardHistory:
    def __init__(self, path: Path | None = None):
        self.path = path or (config.USER_DATA_DIR / "reward_history.json")
        self._lock = threading.RLock()
        self.screens: list[dict] = []
        self.failure_counter = 0
        self.load()

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            screens = data.get("screens", [])
            counter = int(data.get("failure_counter", 0))
        except (OSError, ValueError, TypeError, AttributeError):
            return
        with self._lock:
            self.screens = [s for s in screens if isinstance(s, dict) and s.get("id")][-MAX_SCREENS:]
            numbers = [int(s.get("failure_no") or 0) for s in self.screens]
            self.failure_counter = max([counter, *numbers])

    def save(self):
        tmp_path: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                payload = {"screens": self.screens[-MAX_SCREENS:], "failure_counter": self.failure_counter}
                with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", dir=self.path.parent,
                    prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
                ) as f:
                    tmp_path = Path(f.name)
                    json.dump(payload, f, ensure_ascii=False, indent=1)
                os.replace(tmp_path, self.path)
        except OSError as e:
            print(f"[Rewards] Could not save the reward history: {e}")
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _find(self, screen_id: str) -> dict | None:
        return next((s for s in reversed(self.screens) if s.get("id") == screen_id), None)

    def record(self, screen_id: str, rows: list[dict]):
        """Create or refresh the entry for one reward screen with the rewards read so far."""
        rewards = [
            {
                "display_name": str(r.get("display_name", "")),
                "url_name": str(r.get("url_name", "")),
                "price": r.get("price"),
                "ducats": r.get("ducats"),
                "slot": r.get("slot_index"),
            }
            for r in rows if r.get("url_name")
        ]
        if not rewards:
            return
        with self._lock:
            screen = self._find(screen_id)
            if screen is None:
                screen = {"id": screen_id, "at": time.time(), "picked": "", "added": ""}
                self.screens.append(screen)
                del self.screens[:-MAX_SCREENS]
            screen["rewards"] = rewards
        self.save()

    def mark_picked(self, screen_id: str, url_name: str):
        with self._lock:
            screen = self._find(screen_id)
            if screen is None:
                return
            screen["picked"] = url_name
        self.save()

    def mark_failed(self, screen_id: str) -> int:
        """The pick on this screen could not be confirmed: number it as the next failure.

        Returns the failure number, or 0 when the screen is unknown. Marking the
        same screen twice keeps its first number.
        """
        with self._lock:
            screen = self._find(screen_id)
            if screen is None:
                return 0
            if not screen.get("failure_no"):
                self.failure_counter += 1
                screen["failure_no"] = self.failure_counter
            number = int(screen["failure_no"])
        self.save()
        return number

    def mark_added(self, screen_id: str, url_name: str):
        """The user added this reward to the Item List by hand."""
        with self._lock:
            screen = self._find(screen_id)
            if screen is None:
                return
            screen["added"] = url_name
        self.save()

    def recent(self, limit: int = 30) -> list[dict]:
        with self._lock:
            return list(reversed(self.screens[-limit:]))
