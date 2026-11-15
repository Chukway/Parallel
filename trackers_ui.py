"""Trackers pages: Live, Daily, Weekly, Archimedea, Arbitration and News, plus their Settings tab and tray icon.

``TrackerHub`` owns the data (refreshed every two minutes, counted down every second) and every
page draws from it. The pages hold no data of their own, so they all stay in step.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Callable

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QScrollArea,
    QSystemTrayIcon, QTableWidget, QVBoxLayout, QWidget,
)

import config
import tracker_data as td
import qt_ui
from qt_ui import _StyledCheckBox, _button, _prepare_table, _table_item

def _tier_colors() -> dict[str, str]:
    """Read at draw time so the tiers follow the current theme."""
    return {"S": qt_ui.C_GOLD, "A": qt_ui.C_GREEN, "B": qt_ui.C_BLUE, "C": qt_ui.C_MUTED, "D": qt_ui.C_RED, "F": qt_ui.C_RED}


# ── data hub ────────────────────────────────────────────────────────

class TrackerHub(QObject):
    updated = Signal()          # new data arrived
    layout_changed = Signal()   # a card was turned on or off, or a name changed
    ticked = Signal()           # once a second
    notify = Signal(str, str)   # title, text
    _fetched = Signal(bool)

    REFRESH_SECONDS = 120

    def __init__(self, state=None, ws=None, schedule=None, parent=None):
        super().__init__(parent)
        self.state = state or td.TrackerState()
        self.ws = ws or td.WorldState()
        self.schedule = schedule or td.shared_schedule()
        self.go_to_page: Callable[[str], None] = lambda page: None
        self.go_to_baro: Callable[[], None] = lambda: None
        self.busy = False
        self._fetched.connect(self._finish)
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self.ticked.emit)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(self.REFRESH_SECONDS * 1000)
        self._refresh_timer.timeout.connect(self.refresh)
        self._notify_timer = QTimer(self)
        self._notify_timer.setInterval(30_000)
        self._notify_timer.timeout.connect(self.check_notifications)

    def start(self):
        self.state.prune_ticks()
        self._tick_timer.start()
        self._refresh_timer.start()
        self._notify_timer.start()
        if self.ws.age_seconds() > 60:
            self.refresh()
        QTimer.singleShot(3000, self.check_notifications)

    def stop(self):
        for timer in (self._tick_timer, self._refresh_timer, self._notify_timer):
            timer.stop()

    def now(self) -> datetime:
        return td.now_utc()

    def refresh(self):
        if self.busy:
            return
        self.busy = True
        threading.Thread(
            target=lambda: self._fetched.emit(self.ws.fetch()), name="Parallel: Trackers refresh", daemon=True,
        ).start()

    def _finish(self, _ok: bool):
        self.busy = False
        self.updated.emit()

    def check_notifications(self):
        for title, text in td.due_notifications(self.state, self.schedule, self.ws, self.now()):
            self.notify.emit(title, text)

    def status_text(self) -> str:
        if not self.ws.fetched:
            return "Loading live data..." if self.busy else f"No live data yet. {self.ws.error}".strip()
        age = int(self.ws.age_seconds())
        if self.busy:
            return "Updating..."
        if self.ws.error and age > 300:
            return f"Offline. Showing data from {td.fmt_span(age)} ago; timers keep running."
        return f"Updated {td.fmt_span(age)} ago"

    # settings changes
    def card_changed(self):
        self.layout_changed.emit()


# ── building blocks ─────────────────────────────────────────────────

def _label(text: str = "", muted: bool = False, style: str = "", wrap: bool = True) -> QLabel:
    label = QLabel(text)
    if muted:
        label.setObjectName("Muted")
    label.setWordWrap(wrap)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    if style:
        label.setStyleSheet(style)
    return label


def _big(text: str) -> QLabel:
    return _label(text, style="font-size: 20px; font-weight: 700;", wrap=False)


def _link(text: str, callback: Callable[[], None]) -> QLabel:
    label = QLabel(f'<a href="#" style="color:{qt_ui.C_GOLD}; text-decoration:underline;">{text}</a>')
    label.setCursor(Qt.PointingHandCursor)
    label.linkActivated.connect(lambda _href: callback())
    return label


def _web_link(text: str, url: str) -> QLabel:
    label = QLabel(f'<a href="{url}" style="color:{qt_ui.C_GOLD};">{text}</a>')
    label.setWordWrap(True)
    label.setOpenExternalLinks(False)
    label.linkActivated.connect(lambda href: QDesktopServices.openUrl(QUrl(href)))
    return label


def _tier_tag(tier: str) -> str:
    return f'<span style="color:{_tier_colors().get(tier, qt_ui.C_MUTED)}; font-weight:700;">Tier {tier}</span>'


def make_card(title: str, span: int = 1) -> tuple[QFrame, QVBoxLayout]:
    """A titled panel. ``span`` is how many grid columns it takes; 0 means the whole row."""
    frame = QFrame()
    frame.setObjectName("Panel")
    frame.setProperty("span", span)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(6)
    layout.addWidget(_label(title, muted=True, style="font-weight: 700;"))
    return frame, layout


def _row(left, right=None, spacing: int = 10) -> QWidget:
    holder = QWidget()
    holder.setStyleSheet("background: transparent;")
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 2, 0, 2)
    row.setSpacing(spacing)
    left.setMinimumWidth(0)
    row.addWidget(left, 1)
    if right is not None:
        row.addWidget(right, 0, Qt.AlignRight)
    return holder


class CardGrid(QWidget):
    """Lays cards out in as many columns as fit."""

    def __init__(self, min_card_width: int = 340):
        super().__init__()
        self.min_card_width = min_card_width
        self._cards: list[QWidget] = []
        self._cols = 0
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(12)

    def set_cards(self, cards: list[QWidget]):
        for old in self._cards:
            self._grid.removeWidget(old)
            old.setParent(None)
            old.deleteLater()
        self._cards = list(cards)
        self._relayout()

    def _columns(self) -> int:
        return max(1, self.width() // self.min_card_width)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._columns() != self._cols:
            self._relayout()

    def _relayout(self):
        for card in self._cards:
            self._grid.removeWidget(card)
        cols = self._cols = self._columns()
        for index in range(max(cols, 8)):
            self._grid.setColumnStretch(index, 1 if index < cols else 0)
        row = col = 0
        for card in self._cards:
            wanted = int(card.property("span") or 0)
            span = cols if wanted == 0 else min(wanted, cols)
            if col + span > cols:
                row, col = row + 1, 0
            self._grid.addWidget(card, row, col, 1, span, Qt.AlignTop)
            card.show()
            col += span
            if col >= cols:
                row, col = row + 1, 0


class TrackerPage(QWidget):
    page = ""
    min_card_width = 340

    def __init__(self, hub: TrackerHub):
        super().__init__()
        self.hub = hub
        self.state = hub.state
        self._live: list[tuple[QLabel, Callable[[], str]]] = []
        self._watch: list[datetime] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        bar = QFrame()
        bar.setObjectName("Toolbar")
        bar_row = QHBoxLayout(bar)
        bar_row.setContentsMargins(12, 10, 12, 10)
        bar_row.setSpacing(12)
        self.title = QLabel("")
        self.title.setObjectName("Title")
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        bar_row.addWidget(self.title)
        bar_row.addWidget(self.status, 1)
        self.bar_row = bar_row
        refresh = _button("Refresh", qt_ui.C_BG3, qt_ui.C_TEXT)
        refresh.clicked.connect(hub.refresh)
        bar_row.addWidget(refresh)
        root.addWidget(bar)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 12, 0, 12)
        self.body_layout.setSpacing(12)
        self.scroll.setWidget(self.body)
        root.addWidget(self.scroll, 1)
        self.grid = CardGrid(self.min_card_width)
        self.body_layout.addWidget(self.grid)
        self.body_layout.addStretch(1)
        hub.updated.connect(self.rebuild)
        hub.layout_changed.connect(self.rebuild)
        hub.ticked.connect(self._tick)
        self.rebuild()

    # subclasses
    def build_card(self, card_id: str) -> QWidget | None:
        raise NotImplementedError

    # shared
    def cards_for_page(self) -> list[td.CardDef]:
        return [c for c in td.CARDS if c.page == self.page and self.state.is_on(c.id)]

    def rebuild(self):
        self._live.clear()
        self._watch.clear()
        self.title.setText(self.state.page_title(self.page))
        cards = []
        for definition in self.cards_for_page():
            widget = self.build_card(definition.id)
            if widget is not None:
                cards.append(widget)
        if not cards:
            frame, layout = make_card("Nothing to show")
            layout.addWidget(_label("Every card on this page is turned off, or there is no data yet. "
                                    "Turn cards on in Settings, Trackers tab.", muted=True))
            frame.setProperty("span", 0)
            cards.append(frame)
        self.grid.set_cards(cards)
        self._tick()

    def _tick(self):
        self.status.setText(self.hub.status_text())
        for label, fn in list(self._live):
            try:
                label.setText(fn())
            except RuntimeError:  # the card was replaced
                self._live.remove((label, fn))
        now = self.hub.now()
        if any(expiry <= now for expiry in self._watch):
            self.rebuild()

    def countdown(self, expiry: datetime | None, fmt: str = "{}", watch: bool = False, style: str = "") -> QLabel:
        label = _label(style=style, wrap=False)
        if expiry is None:
            label.setText("")
            return label

        def text() -> str:
            return fmt.format(td.fmt_span((expiry - self.hub.now()).total_seconds()))

        label.setText(text())
        self._live.append((label, text))
        if watch:
            self._watch.append(expiry)
        return label

    def tick_list(self, kind: str, ticks) -> QWidget:
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        for tick_id, text in ticks:
            box = _StyledCheckBox(text)
            box.setChecked(self.state.is_ticked(tick_id, kind, self.hub.now()))
            box.toggled.connect(lambda checked, t=tick_id: self._set_tick(kind, t, checked))
            column.addWidget(box)
        return holder

    def _set_tick(self, kind: str, tick_id: str, checked: bool):
        self.state.set_tick(tick_id, kind, checked, self.hub.now())
        self.hub.layout_changed.emit()


class LazyPage(QWidget):
    """Builds the real page the first time it is shown, so opening the app stays quick."""

    def __init__(self, factory: Callable[[], QWidget]):
        super().__init__()
        self._factory = factory
        self.page: QWidget | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

    def ensure(self) -> QWidget:
        if self.page is None:
            self.page = self._factory()
            self.layout().addWidget(self.page)
        return self.page

    def showEvent(self, event):
        self.ensure()
        super().showEvent(event)


# ── Live ────────────────────────────────────────────────────────────

class LivePage(TrackerPage):
    page = "live"

    def build_card(self, card_id: str) -> QWidget | None:
        return {
            "cycles": self._cycles, "arbitration": self._arbitration, "baro": self._baro,
            "fissures": self._fissures, "storms": self._storms, "bounties": self._bounties,
            "darvo": self._darvo, "alerts": self._alerts, "events": self._events,
            "invasions": self._invasions, "incursions": self._incursions,
        }[card_id](card_id)

    def _title(self, card_id: str) -> str:
        return self.state.title(card_id)

    def _cycles(self, card_id: str):
        rows = td.cycles_view(self.hub.ws, self.hub.now())
        if not rows:
            return None
        frame, layout = make_card(self._title(card_id), span=0)
        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        for row in rows:
            tile = QFrame()
            tile.setObjectName("CycleTile")
            tile.setStyleSheet(f"QFrame#CycleTile {{ background: transparent; border: 1px solid {qt_ui.C_BG3}; border-radius: 4px; }}")
            inner = QVBoxLayout(tile)
            inner.setContentsMargins(12, 8, 12, 8)
            inner.setSpacing(2)
            inner.addWidget(_label(row["name"], muted=True, style="background: transparent;"))
            big = self.countdown(row["expiry"], watch=True, style="font-size: 20px; font-weight: 700; background: transparent;")
            inner.addWidget(big)
            following = f" · {row['next'].title()} next" if row["next"] else ""
            inner.addWidget(_label(f"{row['state'].title()}{following}", style="background: transparent;"))
            tiles.addWidget(tile, 1)
        layout.addLayout(tiles)
        return frame

    def _arbitration(self, card_id: str):
        schedule, now = self.hub.schedule, self.hub.now()
        frame, layout = make_card(self._title(card_id), span=2)
        current = schedule.current(now)
        if current is None:
            layout.addWidget(_label("No Arbitration data for this hour.", muted=True))
        else:
            layout.addWidget(_big(f"{current.name} · {current.mission}"))
            line = QHBoxLayout()
            line.addWidget(_label(f"{current.faction} · {_tier_tag(current.tier)} · ends in", wrap=False))
            line.addWidget(self.countdown(current.end, watch=True, style="font-weight: 600;"))
            line.addStretch(1)
            layout.addLayout(line)
            self._watch.append(current.end)
        layout.addWidget(_label("Next up", muted=True))
        for entry in schedule.upcoming(now, 3):
            left = _label(f"{entry.name} · {entry.mission} · {entry.faction}", wrap=False)
            right = self.countdown(entry.start, fmt=f"Tier {entry.tier} · in {{}}", style=f"color: {_tier_colors().get(entry.tier, qt_ui.C_MUTED)};")
            layout.addWidget(_row(left, right))
        layout.addWidget(_link("Full schedule →", lambda: self.hub.go_to_page("arbitration")))
        return frame

    def _baro(self, card_id: str):
        view = td.baro_view(self.hub.ws)
        frame, layout = make_card(self._title(card_id))
        if view is None or view["start"] is None or view["end"] is None:
            layout.addWidget(_label("Baro's schedule is not available yet.", muted=True))
        else:
            layout.addWidget(_big(view["name"]))
            now = self.hub.now()
            if now < view["start"]:
                layout.addWidget(self.countdown(view["start"], f"Arrives in {{}} at {view['location']}"))
            elif now < view["end"]:
                layout.addWidget(self.countdown(view["end"], f"At {view['location']} · leaves in {{}}"))
            else:
                layout.addWidget(_label("Baro has left. Check back soon.", muted=True))
        layout.addWidget(_link("Go to Baro section", self.hub.go_to_baro))
        return frame

    def _fissure_block(self, title: str, rows: list[dict]) -> QWidget:
        block = QWidget()
        block.setStyleSheet("background: transparent;")
        column = QVBoxLayout(block)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        column.addWidget(_label(f"{title} ({len(rows)})", style="font-weight: 700;"))
        if not rows:
            column.addWidget(_label("None right now.", muted=True))
        for row in rows:
            left = _label(f'<b>{row["tier"]}</b> · {row["mission"]} · {row["node"]}', wrap=False)
            right = self.countdown(row["expiry"], watch=True, style=f"color: {qt_ui.C_MUTED};")
            column.addWidget(_row(left, right))
        column.addStretch(1)
        return block

    def _fissures(self, card_id: str):
        groups = td.fissures_view(self.hub.ws, self.hub.now())
        if not any(groups.values()) and not self.hub.ws.fetched:
            return None
        frame, layout = make_card(self._title(card_id), span=0)
        sides = QHBoxLayout()
        sides.setSpacing(24)
        sides.addWidget(self._fissure_block("Normal", groups["normal"]), 1)
        divider = QFrame()
        divider.setObjectName("Divider")
        divider.setFixedWidth(1)
        sides.addWidget(divider)
        sides.addWidget(self._fissure_block("Steel Path", groups["steel"]), 1)
        layout.addLayout(sides)
        return frame

    def _storms(self, card_id: str):
        frame, layout = make_card(self._title(card_id))
        rows = td.fissures_view(self.hub.ws, self.hub.now())["storm"]
        layout.addWidget(self._fissure_block("Void Storms", rows))
        return frame

    def _simple_list(self, card_id: str, rows: list[tuple[str, datetime | None]], empty: str, span: int = 1):
        frame, layout = make_card(self._title(card_id), span=span)
        if not rows:
            layout.addWidget(_label(empty, muted=True))
        for text, expiry in rows[:12]:
            right = self.countdown(expiry, watch=False, style=f"color: {qt_ui.C_MUTED};") if expiry else None
            layout.addWidget(_row(_label(text, wrap=False), right))
        return frame

    def _bounties(self, card_id: str):
        rows = td.bounties_view(self.hub.ws, self.hub.now())
        return self._simple_list(card_id, [(f"{r['syndicate']} · {r['jobs']} jobs", r["expiry"]) for r in rows], "No bounties right now.")

    def _darvo(self, card_id: str):
        rows = td.darvo_view(self.hub.ws, self.hub.now())
        return self._simple_list(
            card_id, [(f"{r['item']} · {r['price']}p (was {r['was']}p) · {r['left']} left", r["expiry"]) for r in rows],
            "No deal right now.")

    def _alerts(self, card_id: str):
        rows = td.alerts_view(self.hub.ws, self.hub.now())
        return self._simple_list(card_id, [(r["text"], r["expiry"]) for r in rows], "No alerts right now.")

    def _events(self, card_id: str):
        rows = td.events_view(self.hub.ws, self.hub.now())
        return self._simple_list(card_id, [(r["text"], r["expiry"]) for r in rows], "No events right now.")

    def _invasions(self, card_id: str):
        rows = td.invasions_view(self.hub.ws)
        return self._simple_list(
            card_id, [(f"{r['node']} · {r['text']}" + (f" · {float(r['progress']):.0f}%" if isinstance(r["progress"], (int, float)) else ""), None) for r in rows],
            "No invasions right now.", span=2)

    def _incursions(self, card_id: str):
        view = td.steelpath_view(self.hub.ws) or {}
        rows = [(str(item.get("name") or item) if isinstance(item, dict) else str(item), None) for item in view.get("incursions", [])]
        return self._simple_list(card_id, rows, "No incursion data right now.")


# ── Daily ───────────────────────────────────────────────────────────

class DailyPage(TrackerPage):
    page = "daily"

    def build_card(self, card_id: str) -> QWidget | None:
        return {"sortie": self._sortie, "daily_ticks": self._ticks}[card_id](card_id)

    def _sortie(self, card_id: str):
        view = td.sortie_view(self.hub.ws, self.hub.now())
        frame, layout = make_card(self.state.title(card_id), span=2)
        if view is None:
            layout.addWidget(_label("No Sortie data yet.", muted=True))
            return frame
        head = f"{view['boss']} · {view['faction']}".strip(" ·")
        if head:
            layout.addWidget(_big(head))
        for index, mission in enumerate(view["missions"], start=1):
            layout.addWidget(_label(f"<b>{index}</b>  {mission['type']} · {mission['node']}", wrap=False))
            if mission["modifier"]:
                layout.addWidget(_label(f"{mission['modifier']}: {mission['detail']}", muted=True))
        line = QHBoxLayout()
        line.addWidget(_label("Ends in", muted=True, wrap=False))
        line.addWidget(self.countdown(view["expiry"]))
        line.addStretch(1)
        layout.addLayout(line)
        return frame

    def _ticks(self, card_id: str):
        frame, layout = make_card(self.state.title(card_id))
        layout.addWidget(self.countdown(td.next_reset("daily", self.hub.now()), "Resets in {}", style=f"color: {qt_ui.C_MUTED};"))
        layout.addWidget(self.tick_list("daily", td.DAILY_TICKS))
        return frame


# ── Weekly ──────────────────────────────────────────────────────────

class WeeklyPage(TrackerPage):
    page = "weekly"

    def build_card(self, card_id: str) -> QWidget | None:
        return {"circuit": self._circuit, "archon": self._archon, "steelpath": self._steelpath, "weekly_ticks": self._ticks}[card_id](card_id)

    def _circuit(self, card_id: str):
        view = td.circuit_view(self.hub.ws)
        frame, layout = make_card(self.state.title(card_id))
        if view is None:
            layout.addWidget(_label("No Duviri data yet.", muted=True))
            return frame
        layout.addWidget(_label("Warframes", muted=True))
        layout.addWidget(_label(", ".join(view["warframes"]) or "-"))
        layout.addWidget(_label("Steel Path (Incarnon weapons)", muted=True))
        layout.addWidget(_label(", ".join(view["incarnon"]) or "-"))
        layout.addWidget(self.countdown(td.next_reset("weekly", self.hub.now()), "Rotates in {}", style=f"color: {qt_ui.C_MUTED};"))
        return frame

    def _archon(self, card_id: str):
        view = td.archon_view(self.hub.ws)
        frame, layout = make_card(self.state.title(card_id))
        if view is None:
            layout.addWidget(_label("No Archon Hunt data yet.", muted=True))
            return frame
        layout.addWidget(_big(view["boss"] or "Archon Hunt"))
        for index, mission in enumerate(view["missions"], start=1):
            layout.addWidget(_label(f"<b>{index}</b>  {mission['type']} · {mission['node']}", wrap=False))
        layout.addWidget(self.countdown(view["expiry"], "Ends in {}", style=f"color: {qt_ui.C_MUTED};"))
        return frame

    def _steelpath(self, card_id: str):
        view = td.steelpath_view(self.hub.ws)
        frame, layout = make_card(self.state.title(card_id))
        if view is None or not view["reward"]:
            layout.addWidget(_label("No Steel Path data yet.", muted=True))
            return frame
        layout.addWidget(_big(view["reward"]))
        if view["cost"]:
            layout.addWidget(_label(f"{view['cost']} Steel Essence", muted=True))
        rotation = view["rotation"]
        if view["reward"] in rotation and len(rotation) > 1:
            following = rotation[(rotation.index(view["reward"]) + 1) % len(rotation)]
            layout.addWidget(_label(f"Next week: {following}"))
        layout.addWidget(self.countdown(view["expiry"], "Changes in {}", style=f"color: {qt_ui.C_MUTED};"))
        return frame

    def _ticks(self, card_id: str):
        frame, layout = make_card(self.state.title(card_id), span=2)
        layout.addWidget(self.countdown(td.next_reset("weekly", self.hub.now()), "Resets in {} (Monday 00:00 UTC)", style=f"color: {qt_ui.C_MUTED};"))
        layout.addWidget(self.tick_list("weekly", td.WEEKLY_TICKS))
        layout.addWidget(_link("ETA and EDA details →", lambda: self.hub.go_to_page("archimedea")))
        return frame


# ── Archimedea ──────────────────────────────────────────────────────

class ArchimedeaPage(TrackerPage):
    page = "archimedea"
    min_card_width = 520

    def build_card(self, card_id: str) -> QWidget | None:
        view = td.archimedea_view(self.hub.ws).get(card_id)
        frame, layout = make_card(self.state.title(card_id))
        if view is None:
            layout.addWidget(_label("No data for this week yet.", muted=True))
            return frame
        layout.addWidget(_big(view["name"]))
        layout.addWidget(self.countdown(view["expiry"], "Rotates in {}", style=f"color: {qt_ui.C_MUTED};"))
        layout.addWidget(_label("Personal modifiers (debuffs on you)", muted=True, style="font-weight: 700;"))
        for modifier in view["modifiers"]:
            layout.addWidget(_label(f'<span style="color:{qt_ui.C_RED};">●</span> <b>{modifier["name"]}</b>: {modifier["text"]}'))
        for index, mission in enumerate(view["missions"], start=1):
            layout.addWidget(_label(f"Mission {index}: {mission['type']} · {mission['faction']}", muted=True, style="font-weight: 700; padding-top: 6px;"))
            if mission["deviation"]:
                layout.addWidget(_label(f"<b>{mission['deviation']}</b>: {mission['deviation_text']}"))
            for risk in mission["risks"]:
                tag = f'<span style="color:{qt_ui.C_RED}; font-weight:700;">Elite</span> ' if risk["elite"] else ""
                layout.addWidget(_label(f'{tag}<b>{risk["name"]}</b>: {risk["text"]}'))
        layout.addWidget(self.tick_list("weekly", [(card_id, "Done this week")]))
        return frame


# ── News ────────────────────────────────────────────────────────────

class NewsPage(TrackerPage):
    page = "news"
    min_card_width = 480

    def build_card(self, card_id: str) -> QWidget | None:
        return {"news": self._news, "nightwave": self._nightwave}[card_id](card_id)

    def _news(self, card_id: str):
        rows = td.news_view(self.hub.ws)
        frame, layout = make_card(self.state.title(card_id), span=2)
        if not rows:
            layout.addWidget(_label("No news yet.", muted=True))
        now = self.hub.now()
        for row in rows:
            age = td.fmt_span((now - row["date"]).total_seconds())
            left = _web_link(row["message"], row["link"]) if row["link"] else _label(row["message"])
            layout.addWidget(_row(left, _label(f"{age} ago", muted=True, wrap=False)))
        return frame

    def _nightwave(self, card_id: str):
        view = td.nightwave_view(self.hub.ws, self.hub.now())
        frame, layout = make_card(self.state.title(card_id))
        if view is None:
            layout.addWidget(_label("No Nightwave data yet.", muted=True))
            return frame
        layout.addWidget(_big(f"Season {view['season']}" if view["season"] is not None else "Nightwave"))
        for kind, heading in (("daily", "Daily acts"), ("weekly", "Weekly acts"), ("elite", "Elite weekly acts")):
            acts = view["acts"][kind]
            if not acts:
                continue
            layout.addWidget(_label(heading, muted=True, style="font-weight: 700; padding-top: 4px;"))
            for act in acts:
                standing = f" ({act['standing']:,})" if isinstance(act["standing"], int) else ""
                layout.addWidget(_label(f"<b>{act['title']}</b>{standing}: {act['desc']}"))
        return frame


# ── Arbitration schedule ────────────────────────────────────────────

class ArbitrationPage(QWidget):
    page = "arbitration"
    MAX_ROWS = 1500

    def __init__(self, hub: TrackerHub):
        super().__init__()
        self.hub = hub
        self._ticks = 0
        schedule = hub.schedule
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        bar = QFrame()
        bar.setObjectName("Toolbar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(10)
        self.title = QLabel("")
        self.title.setObjectName("Title")
        row.addWidget(self.title)
        row.addStretch(1)

        def combo(label: str, items: list[tuple[str, object]]) -> QComboBox:
            box = QComboBox()
            for text, data in items:
                box.addItem(text, data)
            box.setToolTip(label)
            box.currentIndexChanged.connect(lambda _i: self.reload())
            row.addWidget(QLabel(label + ":"))
            row.addWidget(box)
            return box

        self.range_box = combo("Show", [("Next 24 hours", 24), ("7 days", 24 * 7), ("30 days", 24 * 30), ("90 days", 24 * 90), ("12 months", 24 * 365)])
        self.mission_box = combo("Mission", [("All", "")] + [(m, m) for m in schedule.mission_types()])
        self.tier_box = combo("Tier", [("All", "")] + [(t, t) for t in "SABCDF"])
        self.faction_box = combo("Faction", [("All", "")] + [(f, f) for f in schedule.factions()])
        self.zone_box = combo("Time", [("Local", "local"), ("UTC", "utc")])
        self.clock_box = combo("Clock", [("12-hour", 12), ("24-hour", 24)])
        root.addWidget(bar)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Starts", "In", "Mission", "Node", "Faction", "Tier"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        _prepare_table(self.table, [190, 110, 150, 260, 130, 70], stretch_column=3)
        root.addWidget(self.table, 1)
        self.note = QLabel("")
        self.note.setObjectName("Muted")
        self.note.setContentsMargins(12, 6, 12, 6)
        root.addWidget(self.note)
        hub.ticked.connect(self._tick)
        hub.layout_changed.connect(self._retitle)
        self._retitle()
        self.reload()

    def _retitle(self):
        self.title.setText(self.hub.state.page_title("arbitration"))

    def _tick(self):
        self._ticks += 1
        if self._ticks % 30 == 0:
            self.reload()

    def _format(self, when: datetime) -> str:
        zone = when.astimezone() if self.zone_box.currentData() == "local" else when.astimezone(td.UTC)
        pattern = "%a %b %d, %I:%M %p" if self.clock_box.currentData() == 12 else "%a %b %d, %H:%M"
        text = zone.strftime(pattern)
        return text.replace(" 0", " ", 1) if self.clock_box.currentData() == 12 else text

    def reload(self):
        now = self.hub.now()
        entries = self.hub.schedule.window(
            now, int(self.range_box.currentData()),
            missions=[self.mission_box.currentData()] if self.mission_box.currentData() else None,
            tiers=[self.tier_box.currentData()] if self.tier_box.currentData() else None,
            factions=[self.faction_box.currentData()] if self.faction_box.currentData() else None,
        )
        shown = entries[: self.MAX_ROWS]
        self.table.setRowCount(0)
        for entry in shown:
            r = self.table.rowCount()
            self.table.insertRow(r)
            live = entry.start <= now < entry.end
            wait = "now" if live else td.fmt_span((entry.start - now).total_seconds())
            cells = [self._format(entry.start), wait, entry.mission, entry.name, entry.faction, entry.tier]
            for column, text in enumerate(cells):
                item = _table_item(text, mono=column in (0, 1))
                if live:
                    item.setBackground(QColor(qt_ui.C_BG3))
                    item.setForeground(QColor(qt_ui.C_GOLD))
                elif column == 5:
                    item.setForeground(QColor(_tier_colors().get(entry.tier, qt_ui.C_MUTED)))
                self.table.setItem(r, column, item)
        extra = f" Showing the first {self.MAX_ROWS}; narrow the filters to see the rest." if len(entries) > self.MAX_ROWS else ""
        self.note.setText(
            f"{len(entries)} Arbitrations. Tier ratings come from the Arbitration Goons community "
            f"and can be edited in tracker_arbitration_tiers.json.{extra}"
        )


# ── Settings tab ────────────────────────────────────────────────────

def build_settings_tab(layout: QVBoxLayout, hub: TrackerHub):
    """Fills the Trackers tab of Settings. Everything here applies at once."""
    state = hub.state

    def heading(text: str):
        title = QLabel(text)
        title.setObjectName("Title")
        layout.addWidget(title)

    def note(text: str):
        layout.addWidget(_label(text, muted=True))

    heading("Trackers")
    note("Choose which cards show on each Trackers page and rename them. Changes apply immediately.")

    heading("Page names")
    names = QGridLayout()
    names.setColumnStretch(1, 1)
    for row, (page, default) in enumerate(td.PAGE_TITLES.items()):
        names.addWidget(QLabel(f"{default}:"), row, 0)
        edit = QLineEdit(state.page_title(page))
        edit.setMaxLength(24)
        edit.editingFinished.connect(lambda p=page, e=edit: (state.set_page_title(p, e.text()), hub.card_changed()))
        names.addWidget(edit, row, 1)
    layout.addLayout(names)

    heading("Cards")
    note("Turn a card off to hide it. The ones that start off are rarely needed.")
    for page, page_title in td.PAGE_TITLES.items():
        cards = [c for c in td.CARDS if c.page == page]
        if not cards:
            continue
        layout.addWidget(_label(page_title, muted=True, style="font-weight: 700; padding-top: 6px;"))
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        for row, card in enumerate(cards):
            box = _StyledCheckBox("")
            box.setChecked(state.is_on(card.id))
            box.toggled.connect(lambda checked, c=card.id: (state.set_on(c, checked), hub.card_changed()))
            grid.addWidget(box, row, 0)
            edit = QLineEdit(state.title(card.id))
            edit.setMaxLength(48)
            edit.editingFinished.connect(lambda c=card.id, e=edit: (state.set_title(c, e.text()), hub.card_changed()))
            grid.addWidget(edit, row, 1)
        layout.addLayout(grid)

    heading("Notifications")
    for option, text in (
        ("notify_arbitration", "Pop up when an Arbitration of the chosen tiers starts"),
        ("notify_cetus", "Pop up when it turns day or night on the Plains of Eidolon"),
        ("notify_weekly", "Pop up an hour before the weekly reset"),
        ("notify_sound", "Play the alert sound with these pop-ups"),
    ):
        box = _StyledCheckBox(text)
        box.setChecked(bool(state.option(option)))
        box.toggled.connect(lambda checked, o=option: state.set_option(o, checked))
        layout.addWidget(box)
    tiers_row = QHBoxLayout()
    tiers_row.addWidget(QLabel("Arbitration tiers:"))
    chosen = {str(t).upper() for t in state.option("notify_tiers") or []}
    tier_boxes: dict[str, _StyledCheckBox] = {}

    def save_tiers():
        state.set_option("notify_tiers", [t for t, b in tier_boxes.items() if b.isChecked()])

    for tier in "SABCD":
        box = _StyledCheckBox(tier)
        box.setChecked(tier in chosen)
        box.toggled.connect(lambda _c: save_tiers())
        tier_boxes[tier] = box
        tiers_row.addWidget(box)
    tiers_row.addStretch(1)
    layout.addLayout(tiers_row)

    heading("Window")
    tray = _StyledCheckBox("Closing the window hides Parallel in the system tray instead of quitting")
    tray.setChecked(bool(state.option("tray")))
    tray.toggled.connect(lambda checked: state.set_option("tray", checked))
    layout.addWidget(tray)
    note("Scans and hotkeys keep working while Parallel is in the tray. Right-click the tray icon to open or quit it. "
         "Development copies never use the tray.")
    layout.addStretch(1)


# ── system tray ─────────────────────────────────────────────────────

class TrayController(QObject):
    """Tray icon with Open / Quit, and the pop-up route for notifications while hidden."""

    def __init__(self, window, hub: TrackerHub, icon, quit_app: Callable[[], None]):
        super().__init__(window)
        self.window, self.hub = window, hub
        self.available = QSystemTrayIcon.isSystemTrayAvailable() and not config.IS_DEV_BUILD
        self._hinted = False
        self.tray = QSystemTrayIcon(icon, window)
        self.tray.setToolTip(config.APP_NAME)
        menu = QMenu()
        menu.addAction(f"Open {config.APP_NAME}", self.show_window)
        menu.addSeparator()
        menu.addAction("Quit", quit_app)
        self._menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._activated)
        if self.available:
            self.tray.show()

    def _activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_window()

    def show_window(self):
        QApplication.instance().setQuitOnLastWindowClosed(True)
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def should_hide_on_close(self) -> bool:
        return self.available and bool(self.hub.state.option("tray"))

    def hidden_hint(self):
        if not self._hinted:
            self._hinted = True
            self.tray.showMessage(config.APP_NAME, "Still running in the tray. Right-click the icon to quit.", QSystemTrayIcon.Information, 4000)

    def message(self, title: str, text: str):
        if self.available:
            self.tray.showMessage(title, text, QSystemTrayIcon.Information, 8000)
