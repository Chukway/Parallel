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
            self.screens = [s for s in screens if isinstance(s, dict) and s.get("id")]
            self._trim()
            numbers = [int(s.get("failure_no") or 0) for s in self.screens]
            self.failure_counter = max([counter, *numbers])

    def save(self):
        tmp_path: Path | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                payload = {"screens": list(self.screens), "failure_counter": self.failure_counter}
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

    @property
    def images_dir(self) -> Path:
        return self.path.parent / "reward_failures"

    @staticmethod
    def is_resolved(screen: dict) -> bool:
        return bool(screen.get("added") or screen.get("dismissed"))

    def _trim(self):
        """Keep the newest screens, but never drop a failure that still needs a decision."""
        if len(self.screens) <= MAX_SCREENS:
            return
        excess = len(self.screens) - MAX_SCREENS
        kept, dropped = [], []
        for screen in self.screens:
            removable = excess > len(dropped) and not (screen.get("failure_no") and not self.is_resolved(screen))
            (dropped if removable else kept).append(screen)
        self.screens = kept
        for screen in dropped:
            image = screen.get("image")
            if image:
                try:
                    (self.images_dir / image).unlink(missing_ok=True)
                except OSError:
                    pass

    def record(self, screen_id: str, rows: list[dict]):
        """Create or refresh the entry for one reward screen with the rewards read so far."""
        rewards = []
        for r in rows:
            if not r.get("url_name"):
                continue
            owned = r.get("owned") or {}
            trend = r.get("trend") or {}
            rewards.append({
                "display_name": str(r.get("display_name", "")),
                "url_name": str(r.get("url_name", "")),
                "price": r.get("price"),
                "ducats": r.get("ducats"),
                "slot": r.get("slot_index"),
                "save_for_ducats": bool(r.get("save_for_ducats")),
                "ducat_bulk_value": r.get("ducat_bulk_value"),
                "vaulted": bool(r.get("vaulted")),
                "trend": {"direction": str(trend.get("direction", "")), "arrow": str(trend.get("arrow", ""))} if trend else None,
                "set_name": owned.get("set_name", ""),
                "set_have": owned.get("have"),
                "set_total": owned.get("total"),
            })
        if not rewards:
            return
        with self._lock:
            screen = self._find(screen_id)
            if screen is None:
                screen = {"id": screen_id, "at": time.time(), "picked": "", "added": ""}
                self.screens.append(screen)
                self._trim()
            screen["rewards"] = rewards
        self.save()

    def mark_picked(self, screen_id: str, url_name: str):
        with self._lock:
            screen = self._find(screen_id)
            if screen is None:
                return
            screen["picked"] = url_name
        self.save()

    def mark_failed(self, screen_id: str, unread: list[dict] | None = None, image: str = "",
                    create: bool = False) -> int:
        """The pick on this screen could not be confirmed: number it as the next failure.

        ``unread`` lists the cards that could not be read ({slot, text, candidates}),
        ``image`` is the saved screenshot's file name. ``create`` makes an entry for a
        screen where nothing at all was read. Returns the failure number, or 0 when
        the screen is unknown. Marking the same screen twice keeps its first number.
        """
        with self._lock:
            screen = self._find(screen_id)
            if screen is None:
                if not create:
                    return 0
                screen = {"id": screen_id, "at": time.time(), "picked": "", "added": "", "rewards": []}
                self.screens.append(screen)
                self._trim()
            if not screen.get("failure_no"):
                self.failure_counter += 1
                screen["failure_no"] = self.failure_counter
            if unread is not None:
                screen["unread"] = [
                    {
                        "slot": u.get("slot"),
                        "text": str(u.get("text", ""))[:80],
                        "candidates": [
                            {"display_name": str(c.get("display_name", "")), "url_name": str(c.get("url_name", ""))}
                            for c in (u.get("candidates") or [])[:6] if c.get("url_name")
                        ],
                    }
                    for u in unread
                ]
            if image:
                screen["image"] = image
            number = int(screen["failure_no"])
        self.save()
        return number

    def mark_dismissed(self, screen_id: str):
        """The user looked at this failure and nothing needs adding (for example, they took Forma)."""
        with self._lock:
            screen = self._find(screen_id)
            if screen is None:
                return
            screen["dismissed"] = True
        self.save()

    def failures(self, unresolved_only: bool = True) -> list[dict]:
        """Numbered failures, oldest first."""
        with self._lock:
            found = [s for s in self.screens if s.get("failure_no")]
            if unresolved_only:
                found = [s for s in found if not self.is_resolved(s)]
            return sorted(found, key=lambda s: int(s["failure_no"]))

    def image_path(self, screen: dict) -> Path | None:
        name = screen.get("image")
        if not name or "/" in name or "\\" in name:
            return None
        path = self.images_dir / name
        return path if path.is_file() else None

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
