"""Trading Tools page: price watchlist, repricing, sales log and Baro Ki'Teer.

Also holds ``SetCompareDialog`` used by the Item List ("Set vs parts").
Every market lookup runs on a background thread; results come back through a
Qt signal. Nothing is written to warframe.market without a confirmation.
"""

from __future__ import annotations

import random
import threading
import time
from datetime import datetime
from typing import Callable

from PySide6.QtCore import QDateTime, QStringListModel, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QCompleter, QDateTimeEdit, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QSpinBox, QTableWidget, QTabWidget, QVBoxLayout, QWidget,
)

import config
import trade_tools
from qt_ui import (
    C_BG3, C_BLUE, C_GREEN, C_MUTED, C_RED, C_TEXT,
    NumberEditor, _StyledCheckBox, _button, _market_item_names, _prepare_table, _table_item,
)

WATCH_POLL_MINUTES = 10


def _run_in_thread(name: str, target: Callable[[], None]):
    threading.Thread(target=target, name=f"Parallel: {name}", daemon=True).start()


def _bar(*widgets, stretch_first: bool = False) -> QFrame:
    frame = QFrame()
    frame.setObjectName("Toolbar")
    row = QHBoxLayout(frame)
    row.setContentsMargins(12, 10, 12, 10)
    row.setSpacing(10)
    for index, widget in enumerate(widgets):
        if widget is None:
            row.addStretch(1)
        else:
            row.addWidget(widget, 1 if stretch_first and index == 0 else 0)
    return frame


def _muted(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("Muted")
    label.setWordWrap(True)
    return label


# ── watchlist: price alerts (sell) and wishlist (buy) ───────────────

class WatchEditDialog(QDialog):
    """Change the type and target price of a watchlist entry."""

    def __init__(self, parent, name: str, direction: str, target: int):
        super().__init__(parent)
        self.setWindowTitle("Edit watchlist item")
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(name))
        self.kind = QComboBox()
        self.kind.addItem("Buy: alert when someone sells at or under", "below")
        self.kind.addItem("Sell: alert when the price reaches or passes", "above")
        self.kind.setCurrentIndex(0 if direction == "below" else 1)
        layout.addWidget(self.kind)
        self.target = QSpinBox()
        self.target.setRange(1, 99999)
        self.target.setValue(int(target))
        self.target.setSuffix(" p")
        layout.addWidget(self.target)
        buttons = QHBoxLayout()
        cancel = _button("Cancel", C_BG3, C_TEXT)
        save = _button("Save", C_GREEN, "white")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self.accept)
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)


class WatchAddDialog(QDialog):
    """Choose how items sent from the Item List or Mastery List are watched."""

    def __init__(self, parent, count: int):
        super().__init__(parent)
        self.setWindowTitle("Add to watchlist")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Add {count} item(s) to the watchlist."))
        self.kind = QComboBox()
        self.kind.addItem("Buy: alert when someone sells at or under my target", "below")
        self.kind.addItem("Sell: alert when the price reaches or passes my target", "above")
        layout.addWidget(self.kind)
        self.use_market = QCheckBox("Set each target to its current lowest price")
        self.use_market.setChecked(True)
        layout.addWidget(self.use_market)
        row = QHBoxLayout()
        row.addWidget(QLabel("Otherwise use this target:"))
        self.target = QSpinBox()
        self.target.setRange(1, 99999)
        self.target.setValue(10)
        self.target.setSuffix(" p")
        row.addWidget(self.target)
        layout.addLayout(row)
        layout.addWidget(_muted("You can change any target later with Edit selected."))
        buttons = QHBoxLayout()
        cancel = _button("Cancel", C_BG3, C_TEXT)
        add = _button("Add", C_GREEN, "white")
        cancel.clicked.connect(self.reject)
        add.clicked.connect(self.accept)
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(add)
        layout.addLayout(buttons)


class BuyListingDialog(QDialog):
    """Review a buy listing before it is posted to warframe.market."""

    def __init__(self, parent, name: str, snapshot: dict | None, price: int):
        super().__init__(parent)
        self.setWindowTitle("Post buy listing")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        title = QLabel(name)
        title.setStyleSheet("font-weight: 700; font-size: 15px;")
        layout.addWidget(title)
        snap = snapshot or {}

        def fmt(value):
            return "—" if value is None else f"{value}p"

        layout.addWidget(_muted(
            f"Lowest sell {fmt(snap.get('lowest_sell'))}   ·   Average sell {fmt(snap.get('avg_sell'))}   ·   "
            f"Top buy order {fmt(snap.get('top_buy'))}   ·   Suggested {fmt(snap.get('suggested_buy'))}"
            if snapshot else "Press Update prices first to see what the market looks like."
        ))
        row = QHBoxLayout()
        row.addWidget(QLabel("Price each"))
        self.price = QSpinBox()
        self.price.setRange(1, 99999)
        self.price.setValue(max(1, int(price)))
        self.price.setSuffix(" p")
        row.addWidget(self.price)
        row.addWidget(QLabel("Quantity"))
        self.quantity = QSpinBox()
        self.quantity.setRange(1, 999)
        row.addWidget(self.quantity)
        layout.addLayout(row)
        self.hidden = QCheckBox("Post hidden (not shown on the market until you show it on the website)")
        layout.addWidget(self.hidden)
        layout.addWidget(_muted(
            "This creates a real buy listing on warframe.market. If you already have a buy listing "
            "for this item, its price and quantity are changed instead."
        ))
        buttons = QHBoxLayout()
        cancel = _button("Cancel", C_BG3, C_TEXT)
        post = _button("Post buy listing", C_GREEN, "white")
        cancel.clicked.connect(self.reject)
        post.clicked.connect(self.accept)
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(post)
        layout.addLayout(buttons)


class WatchlistTab(QWidget):
    checked = Signal(object, bool)     # triggered entries, silent
    snapshots_ready = Signal(object)   # {slug: snapshot dict or None}
    added = Signal(object, str)        # [(slug, name, target)], direction
    posted = Signal(str, str)          # message, status

    def __init__(self, get_api, notify, store: trade_tools.WatchStore | None = None):
        super().__init__()
        self.get_api, self._notify = get_api, notify
        self.store = store or trade_tools.WatchStore()
        self.snapshots: dict[str, dict | None] = {}
        self._busy = False
        self.checked.connect(self._finish_check)
        self.snapshots_ready.connect(self._finish_snapshots)
        self.added.connect(self._finish_add)
        self.posted.connect(lambda message, status: self._notify(message, status, 9000))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Item name, e.g. Gyre Prime Chassis Blueprint")
        self.name_input.setMinimumWidth(280)
        self.name_input.setClearButtonEnabled(True)
        self._names: dict[str, str] = {}
        self._model = QStringListModel([])   # kept on self: Qt does not own it, a local would be freed
        self._refresh_names()
        completer = QCompleter(self._model, self.name_input)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.name_input.setCompleter(completer)
        self.name_input.returnPressed.connect(self._add)
        self.kind = QComboBox()
        self.kind.addItem("Buy: alert when someone sells at or under", "below")
        self.kind.addItem("Sell: alert when the price reaches or passes", "above")
        self.target = QSpinBox()
        self.target.setRange(1, 99999)
        self.target.setValue(10)
        self.target.setSuffix(" p")
        add = _button("Add", C_GREEN, "white")
        add.clicked.connect(self._add)
        layout.addWidget(_bar(self.name_input, self.kind, self.target, add, stretch_first=True))

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Item", "Alert when", "Target", "Lowest sell", "Average sell", "Top buy order", "Suggested buy"]
        )
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        _prepare_table(self.table, [280, 130, 80, 100, 110, 120, 120], stretch_column=0)
        self.table.doubleClicked.connect(lambda _i: self._edit())
        layout.addWidget(self.table, 1)

        self.info = _muted(
            f"Checked every {WATCH_POLL_MINUTES} minutes while Parallel is open, using prices from players "
            "who are in game. An item alerts once, then stays quiet for 6 hours."
        )
        layout.addWidget(_bar(self.info))
        remove = _button("Remove selected", C_BG3, C_TEXT)
        remove.clicked.connect(self._remove)
        edit = _button("Edit selected", C_BG3, C_TEXT)
        edit.clicked.connect(self._edit)
        select_all = _button("Select all", C_BG3, C_TEXT)
        select_all.clicked.connect(self.table.selectAll)
        update = _button("Update prices", C_BLUE, "white")
        update.setToolTip("Looks at the current sell and buy prices for every item on this list")
        update.clicked.connect(self.update_prices)
        check = _button("Check now", C_BG3, C_TEXT)
        check.clicked.connect(lambda: self.check_now(silent=False))
        post = _button("Post buy listing", C_GREEN, "white")
        post.setToolTip("Creates a buy listing on warframe.market for the selected item, after you review it")
        post.clicked.connect(self._post_buy)
        layout.addWidget(_bar(select_all, remove, edit, None, update, check, post))

        self.timer = QTimer(self)
        self.timer.setInterval(WATCH_POLL_MINUTES * 60 * 1000)
        self.timer.timeout.connect(lambda: self.check_now(silent=True))
        self.timer.start()
        self.reload()

    def _refresh_names(self):
        """The item-name list may not exist yet at start-up, so reload it when the tab opens."""
        names = _market_item_names()
        if names and names != self._names:
            self._names = names
            self._model.setStringList(sorted(names))

    def on_shown(self):
        self._refresh_names()

    def reload(self):
        self.table.setRowCount(0)
        for entry in self.store.entries:
            row = self.table.rowCount()
            self.table.insertRow(row)
            name = _table_item(entry.name)
            name.setData(Qt.UserRole, (entry.slug, entry.direction))
            self.table.setItem(row, 0, name)
            self.table.setItem(row, 1, _table_item("Buy (wishlist)" if entry.direction == "below" else "Sell alert"))
            self.table.setItem(row, 2, _table_item(f"{entry.target}p", mono=True))
            snap = self.snapshots.get(entry.slug) or {}
            lowest = snap.get("lowest_sell", entry.last_price)

            def cell(value):
                return "—" if value is None else f"{value}p"

            self.table.setItem(row, 3, _table_item(cell(lowest), mono=True))
            self.table.setItem(row, 4, _table_item(cell(snap.get("avg_sell")), mono=True))
            self.table.setItem(row, 5, _table_item(cell(snap.get("top_buy")), mono=True))
            self.table.setItem(row, 6, _table_item(cell(snap.get("suggested_buy")), mono=True))

    def _slug_for_name(self, name: str) -> str | None:
        slug = self._names.get(name)
        if slug is None:
            lowered = {k.casefold(): v for k, v in self._names.items()}
            slug = lowered.get(name.strip().casefold())
        return slug

    def add_entry(self, name: str, direction: str, target: int) -> bool:
        slug = self._slug_for_name(name)
        if slug is None:
            self._notify("Pick an item name from the list so Parallel knows which item you mean.", "warning", 5000)
            return False
        display = next((k for k, v in self._names.items() if v == slug), name)
        self.store.set(slug, display, direction, target)
        self.reload()
        return True

    def _add(self):
        if self.add_entry(self.name_input.text().strip(), str(self.kind.currentData()), self.target.value()):
            self.name_input.clear()

    def _selected_entries(self) -> list[tuple[str, str]]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.table.item(r, 0).data(Qt.UserRole) for r in rows if self.table.item(r, 0) is not None]

    def _remove(self):
        for slug, direction in self._selected_entries():
            self.store.remove(slug, direction)
        self.reload()

    def _find(self, slug: str, direction: str):
        return next((e for e in self.store.entries if e.slug == slug and e.direction == direction), None)

    def _edit(self):
        chosen = self._selected_entries()
        if not chosen:
            self._notify("Select an item to edit first.", "warning", 3000)
            return
        slug, direction = chosen[0]
        entry = self._find(slug, direction)
        if entry is None:
            return
        dialog = WatchEditDialog(self, entry.name, entry.direction, entry.target)
        if dialog.exec() != QDialog.Accepted:
            return
        new_direction = str(dialog.kind.currentData())
        if new_direction != entry.direction:
            self.store.remove(slug, direction)
        self.store.set(slug, entry.name, new_direction, dialog.target.value())
        self.reload()

    # ── from the Item List / Mastery List ────────────────────────
    def add_from_pages(self, items: list[tuple[str, str]]):
        """Add (slug, name) pairs sent from another tab, asking how to watch them."""
        if not items:
            return
        dialog = WatchAddDialog(self, len(items))
        if dialog.exec() != QDialog.Accepted:
            return
        direction = str(dialog.kind.currentData())
        fixed = dialog.target.value()
        api = self.get_api() if dialog.use_market.isChecked() else None

        def work():
            result = []
            for slug, name in items:
                target = fixed
                if api is not None:
                    try:
                        target = int(api.get_price_info(slug).lowest_sell_price)
                    except Exception:
                        target = fixed
                result.append((slug, name, target))
            self.added.emit(result, direction)

        if api is None:
            self.added.emit([(slug, name, fixed) for slug, name in items], direction)
        else:
            _run_in_thread("Watchlist add", work)

    def _finish_add(self, items, direction: str):
        for slug, name, target in items:
            self.store.set(slug, name, direction, target)
        self.reload()
        self._notify(f"{len(items)} item(s) added to the watchlist.", "success", 4000)

    # ── market prices ────────────────────────────────────────────
    def update_prices(self):
        api = self.get_api()
        if api is None:
            self._notify("Log in to warframe.market first (Login / Token).", "warning", 4000)
            return
        slugs = sorted({e.slug for e in self.store.entries})
        if not slugs or self._busy:
            return
        self._busy = True
        self._notify("Looking up current prices...", "info", 3000)

        def work():
            result = {}
            for slug in slugs:
                try:
                    result[slug] = trade_tools.market_snapshot(api, slug)
                except Exception as e:
                    print(f"[Watch] No market data for {slug}: {e}")
                    result[slug] = None
            self.snapshots_ready.emit(result)

        _run_in_thread("Watchlist prices", work)

    def _finish_snapshots(self, result):
        self._busy = False
        self.snapshots.update(result)
        self.reload()
        self._notify("Prices updated.", "success", 3000)

    # ── buy listing ──────────────────────────────────────────────
    def _post_buy(self):
        api = self.get_api()
        if api is None:
            self._notify("Log in to warframe.market first (Login / Token).", "warning", 4000)
            return
        chosen = self._selected_entries()
        if not chosen:
            self._notify("Select the item to buy first.", "warning", 3000)
            return
        slug, direction = chosen[0]
        entry = self._find(slug, direction)
        if entry is None:
            return
        snap = self.snapshots.get(slug)
        price = (snap or {}).get("suggested_buy") or entry.target
        dialog = BuyListingDialog(self, entry.name, snap, int(price))
        if dialog.exec() != QDialog.Accepted:
            return
        price, quantity, visible = dialog.price.value(), dialog.quantity.value(), not dialog.hidden.isChecked()

        def work():
            try:
                action, _order = api.post_buy_order(slug, price, quantity, visible)
                verb = "posted" if action == "created" else "updated"
                self.posted.emit(f"Buy listing {verb}: {entry.name} at {price}p x {quantity}.", "success")
            except Exception as e:
                self.posted.emit(f"Could not post the buy listing: {e}", "error")

        _run_in_thread("Post buy listing", work)

    # ── alerts ───────────────────────────────────────────────────
    def check_now(self, silent: bool = True):
        api = self.get_api()
        if self._busy or api is None or not self.store.entries:
            if not silent and api is None:
                self._notify("Log in to warframe.market first (Login / Token).", "warning", 4000)
            return
        self._busy = True

        def price(slug: str):
            try:
                return int(api.get_price_info(slug).lowest_sell_price)
            except Exception:
                return None

        def work():
            try:
                triggered = self.store.check(price)
            except Exception as e:
                print(f"[Watch] Check failed: {e}")
                triggered = []
            self.checked.emit(triggered, silent)

        _run_in_thread("Watchlist check", work)

    def _finish_check(self, triggered, silent: bool):
        self._busy = False
        self.reload()
        for entry in triggered:
            verb = "is down to" if entry.direction == "below" else "has reached"
            self._notify(f"{entry.name} {verb} {entry.last_price}p (your target: {entry.target}p).", "success", 12000)
        if not silent and not triggered:
            self._notify("Checked your watchlist: nothing has hit its target yet.", "info", 4000)


# ── repricing helper ────────────────────────────────────────────────

class RepriceTab(QWidget):
    rows_ready = Signal(object, str)
    applied = Signal(int, object)

    def __init__(self, get_api, notify):
        super().__init__()
        self.get_api, self._notify = get_api, notify
        self.rows: list[trade_tools.RepriceRow] = []
        self._busy = False
        self.rows_ready.connect(self._finish_check)
        self.applied.connect(self._finish_apply)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.status = _muted(
            "Finds your sell listings that a player in game is currently undercutting, and suggests "
            "a new price. Nothing changes until you tick rows and press Apply."
        )
        self.check_button = _button("Check my listings", C_BLUE, "white")
        self.check_button.clicked.connect(self.check)
        layout.addWidget(_bar(self.status, self.check_button, stretch_first=True))
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Use", "Item", "Qty", "Your price", "Lowest now", "New price"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        _prepare_table(self.table, [42, 300, 60, 100, 100, 130], stretch_column=1)
        layout.addWidget(self.table, 1)
        self.apply_button = _button("Apply ticked", C_GREEN, "white")
        self.apply_button.clicked.connect(self.apply)
        self.apply_button.setEnabled(False)
        self.select_all_button = _button("Select all", C_BG3, C_TEXT)
        self.select_all_button.clicked.connect(lambda: self._set_all(True))
        self.select_none_button = _button("Select none", C_BG3, C_TEXT)
        self.select_none_button.clicked.connect(lambda: self._set_all(False))
        layout.addWidget(_bar(self.select_all_button, self.select_none_button, None, self.apply_button))

    def _set_all(self, checked: bool):
        for row in range(self.table.rowCount()):
            box = self.table.cellWidget(row, 0)
            if isinstance(box, _StyledCheckBox):
                box.setChecked(checked)

    def check(self):
        api = self.get_api()
        if api is None:
            self._notify("Log in to warframe.market first (Login / Token).", "warning", 4000)
            return
        if self._busy:
            return
        self._busy = True
        self.check_button.setEnabled(False)
        self.status.setText("Checking your listings against the market...")
        names = {slug: name for name, slug in _market_item_names().items()}

        def work():
            try:
                rows = trade_tools.build_reprice_rows(api, names)
                self.rows_ready.emit(rows, "")
            except Exception as e:
                self.rows_ready.emit([], str(e))

        _run_in_thread("Repricing check", work)

    def _finish_check(self, rows, error: str):
        self._busy = False
        self.check_button.setEnabled(True)
        self.rows = list(rows)
        self.table.setRowCount(0)
        if error:
            self.status.setText(f"Could not check your listings: {error}")
            self.apply_button.setEnabled(False)
            return
        for entry in self.rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            use = _StyledCheckBox()
            self.table.setCellWidget(row, 0, use)
            self.table.setItem(row, 1, _table_item(entry.name))
            self.table.setItem(row, 2, _table_item(entry.quantity, mono=True))
            self.table.setItem(row, 3, _table_item(f"{entry.my_price}p", mono=True))
            self.table.setItem(row, 4, _table_item(f"{entry.market_low}p", mono=True))
            self.table.setCellWidget(row, 5, NumberEditor(config.MIN_LISTING_PRICE, 99999, entry.suggested))
        self.apply_button.setEnabled(bool(self.rows))
        self.status.setText(
            f"{len(self.rows)} listing(s) are undercut. Tick the ones to lower, adjust the price if you like, then Apply."
            if self.rows else "All your listings are at or below the lowest in-game price. Nothing to change."
        )

    def apply(self):
        chosen = []
        for row, entry in enumerate(self.rows):
            box = self.table.cellWidget(row, 0)
            editor = self.table.cellWidget(row, 5)
            if isinstance(box, _StyledCheckBox) and box.isChecked():
                entry.suggested = int(editor.value())
                chosen.append(entry)
        if not chosen:
            self._notify("Tick at least one listing first.", "warning", 3000)
            return
        lines = "\n".join(f"• {e.name}: {e.my_price}p → {e.suggested}p" for e in chosen[:12])
        more = f"\n…and {len(chosen) - 12} more" if len(chosen) > 12 else ""
        answer = QMessageBox.question(
            self, "Change these listings?",
            f"Update {len(chosen)} listing(s) on warframe.market?\n\n{lines}{more}",
        )
        if answer != QMessageBox.Yes:
            return
        api = self.get_api()
        self.apply_button.setEnabled(False)

        def pause():
            time.sleep(random.uniform(config.LISTING_REQUEST_DELAY_MIN_SECONDS, config.LISTING_REQUEST_DELAY_MAX_SECONDS))

        def work():
            done, errors = trade_tools.apply_reprice(api, chosen, delay=pause)
            self.applied.emit(done, errors)

        _run_in_thread("Repricing apply", work)

    def _finish_apply(self, done: int, errors):
        if errors:
            self._notify(f"{done} updated, {len(errors)} failed. First problem: {errors[0]}", "warning", 12000)
        else:
            self._notify(f"{done} listing(s) updated.", "success", 5000)
        self.check()


# ── sales log ───────────────────────────────────────────────────────

class SaleDialog(QDialog):
    """Add a sale by hand, or fix one in the log (for example to give an old sale a price)."""

    def __init__(self, parent, names: dict[str, str], sale: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit sale" if sale else "Add sale")
        self.setMinimumWidth(440)
        self._names = names
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Item name (start typing and pick from the list):"))
        self.name_input = QLineEdit((sale or {}).get("display_name", ""))
        self.name_input.setClearButtonEnabled(True)
        self._model = QStringListModel(sorted(names))
        completer = QCompleter(self._model, self.name_input)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.name_input.setCompleter(completer)
        layout.addWidget(self.name_input)
        row = QHBoxLayout()
        row.addWidget(QLabel("Quantity"))
        self.quantity = QSpinBox()
        self.quantity.setRange(1, 9999)
        self.quantity.setValue(int((sale or {}).get("count") or 1))
        row.addWidget(self.quantity)
        row.addWidget(QLabel("Total platinum"))
        self.platinum = QSpinBox()
        self.platinum.setRange(0, 9_999_999)
        self.platinum.setSuffix(" p")
        self.platinum.setValue(int((sale or {}).get("platinum") or 0))
        row.addWidget(self.platinum)
        layout.addLayout(row)
        when = datetime.fromtimestamp(float((sale or {}).get("at") or time.time()))
        self.when = QDateTimeEdit(QDateTime(when))
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("yyyy-MM-dd HH:mm")
        layout.addWidget(self.when)
        self.take = QCheckBox("Also take it out of my Item List")
        self.take.setChecked(sale is None)
        self.take.setVisible(sale is None)
        layout.addWidget(self.take)
        layout.addWidget(_muted("This only changes Parallel's log. Your warframe.market listings are not touched."))
        self.error = QLabel("")
        self.error.setStyleSheet(f"color: {C_RED};")
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        cancel = _button("Cancel", C_BG3, C_TEXT)
        save = _button("Save", C_GREEN, "white")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._save)
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def _save(self):
        name = self.name_input.text().strip()
        folded = {k.casefold(): (k, v) for k, v in self._names.items()}
        match = folded.get(name.casefold())
        if match is None:
            self.error.setText("Pick an item name from the list.")
            return
        self.display_name, self.url_name = match
        self.accept()

    def result_at(self) -> float:
        return float(self.when.dateTime().toSecsSinceEpoch())


class SalesTab(QWidget):
    def __init__(self, store, notify):
        super().__init__()
        self.store, self._notify = store, notify
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.summary = _muted("")
        add = _button("Add sale", C_GREEN, "white")
        add.setToolTip("Record a sale by hand, for example one the trade monitor missed")
        add.clicked.connect(self.add_sale)
        edit = _button("Edit selected", C_BG3, C_TEXT)
        edit.clicked.connect(self.edit_sale)
        delete = _button("Delete selected", C_RED, "white")
        delete.clicked.connect(self.delete_sale)
        export = _button("Export CSV", C_BG3, C_TEXT)
        export.clicked.connect(self.export_csv)
        layout.addWidget(_bar(self.summary, add, edit, delete, export, stretch_first=True))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Date", "Item", "Qty", "Platinum"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        _prepare_table(self.table, [160, 360, 60, 110], stretch_column=1)
        self.table.doubleClicked.connect(lambda _i: self.edit_sale())
        layout.addWidget(self.table, 1)
        self.reload()

    def _selected_sale(self) -> dict | None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.data(Qt.UserRole) if item is not None else None

    def add_sale(self):
        dialog = SaleDialog(self, _market_item_names())
        if dialog.exec() != QDialog.Accepted:
            return
        self.store.add_sale(
            dialog.url_name, dialog.display_name, dialog.quantity.value(),
            dialog.platinum.value(), dialog.result_at(), take_from_items=dialog.take.isChecked(),
        )
        self.reload()
        self._notify("Sale added.", "success", 3000)

    def edit_sale(self):
        sale = self._selected_sale()
        if sale is None:
            self._notify("Select a sale to edit first.", "warning", 3000)
            return
        dialog = SaleDialog(self, _market_item_names(), sale)
        if dialog.exec() != QDialog.Accepted:
            return
        if self.store.delete_sale(sale):
            self.store.add_sale(
                dialog.url_name, dialog.display_name, dialog.quantity.value(),
                dialog.platinum.value(), dialog.result_at(),
            )
        self.reload()

    def delete_sale(self):
        sale = self._selected_sale()
        if sale is None:
            self._notify("Select a sale to delete first.", "warning", 3000)
            return
        answer = QMessageBox.question(
            self, "Delete this sale?",
            f"Remove {sale.get('display_name') or sale.get('url_name')} from the sales log?\n\n"
            "This does not put the item back in your Item List.",
        )
        if answer == QMessageBox.Yes and self.store.delete_sale(sale):
            self.reload()
    def reload(self):
        sales = list(getattr(self.store, "sales", []) or [])
        stats = trade_tools.sales_summary(sales)
        text = (
            f"Today {stats['today']}p   ·   Last 7 days {stats['week']}p   ·   All time {stats['platinum']}p "
            f"({stats['items']} items in {stats['sales']} sales)"
        )
        if stats["top"]:
            best = stats["top"][0]
            text += f"   ·   Best seller: {best['name']} ({best['platinum']}p)"
        if stats["unpriced"]:
            text += f"\n{stats['unpriced']} older sale(s) have no price saved; only new sales record platinum."
        self.summary.setText(text)
        self.table.setRowCount(0)
        for sale in reversed(sales[-500:]):
            row = self.table.rowCount()
            self.table.insertRow(row)
            when = datetime.fromtimestamp(float(sale.get("at") or 0)).strftime("%Y-%m-%d %H:%M")
            plat = int(sale.get("platinum") or 0)
            date_item = _table_item(when, mono=True)
            date_item.setData(Qt.UserRole, sale)
            self.table.setItem(row, 0, date_item)
            self.table.setItem(row, 1, _table_item(sale.get("display_name") or sale.get("url_name")))
            self.table.setItem(row, 2, _table_item(sale.get("count", 1), mono=True))
            self.table.setItem(row, 3, _table_item(f"{plat}p" if plat else "—", mono=True))

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export sales", "parallel_sales.csv", "CSV files (*.csv)")
        if not path:
            return
        try:
            count = trade_tools.sales_to_csv(list(getattr(self.store, "sales", []) or []), path)
        except OSError as e:
            self._notify(f"Could not save the file: {e}", "error", 6000)
            return
        self._notify(f"Exported {count} sale(s).", "success", 4000)


# ── Baro Ki'Teer ────────────────────────────────────────────────────

class BaroTab(QWidget):
    loaded = Signal(object, str)

    def __init__(self, get_api, notify):
        super().__init__()
        self.get_api, self._notify = get_api, notify
        self._busy = False
        self._loaded_once = False
        self.loaded.connect(self._finish)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.status = _muted("Open this tab to load Baro Ki'Teer's stock.")
        refresh = _button("Refresh", C_BLUE, "white")
        refresh.clicked.connect(self.refresh)
        layout.addWidget(_bar(self.status, refresh, stretch_first=True))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Item", "Ducats", "Credits", "Market price", "Plat per 100 ducats"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        _prepare_table(self.table, [320, 80, 100, 110, 150], stretch_column=0)
        layout.addWidget(self.table, 1)
        layout.addWidget(_bar(_muted(
            "Market price is the lowest in-game sell price. The last column ranks which "
            "purchases turn the most ducats into platinum. Items that cannot be traded show a dash."
        )))

    def on_shown(self):
        if not self._loaded_once:
            self.refresh()

    def refresh(self):
        if self._busy:
            return
        self._busy = True
        self._loaded_once = True
        self.status.setText("Loading Baro Ki'Teer...")
        api = self.get_api()
        names = _market_item_names()

        def price(slug: str):
            return int(api.get_price_info(slug).lowest_sell_price)

        def work():
            try:
                data = trade_tools.fetch_baro()
                rows = trade_tools.baro_rows(data, names, price if api is not None else None)
                self.loaded.emit({"status": trade_tools.baro_status(data), "rows": rows}, "")
            except Exception as e:
                self.loaded.emit(None, str(e))

        _run_in_thread("Baro lookup", work)

    def _finish(self, result, error: str):
        self._busy = False
        if result is None:
            self._loaded_once = False
            self.status.setText(f"Could not load Baro's stock: {error}")
            return
        self.status.setText(result["status"])
        self.table.setRowCount(0)
        for entry in result["rows"]:
            row = self.table.rowCount()
            self.table.insertRow(row)
            price = entry["price"]
            per = entry["plat_per_100_ducats"]
            self.table.setItem(row, 0, _table_item(entry["name"]))
            self.table.setItem(row, 1, _table_item(entry["ducats"], mono=True))
            self.table.setItem(row, 2, _table_item(f"{entry['credits']:,}", mono=True))
            self.table.setItem(row, 3, _table_item("—" if price is None else f"{price}p", mono=True))
            self.table.setItem(row, 4, _table_item("—" if per is None else per, mono=True))


# ── reward history ──────────────────────────────────────────────────

class RewardHistoryTab(QWidget):
    """Recent reward screens, so a pick the app missed can be added by hand."""

    def __init__(self, store, notify):
        super().__init__()
        from reward_history import RewardHistory
        self.store, self._notify = store, notify
        self.history = RewardHistory()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.info = _muted(
            "Every reward screen Parallel read, newest first. If it missed which one you took, select that "
            "reward and press Add to Item List."
        )
        refresh = _button("Refresh", C_BG3, C_TEXT)
        refresh.clicked.connect(self.reload)
        layout.addWidget(_bar(self.info, refresh, stretch_first=True))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["When", "Reward", "Price", "Ducats", "Result"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        _prepare_table(self.table, [150, 340, 80, 80, 150], stretch_column=1)
        layout.addWidget(self.table, 1)
        add = _button("Add to Item List", C_GREEN, "white")
        add.setToolTip("Adds one of the selected reward to your Item List (it does not touch warframe.market)")
        add.clicked.connect(self.add_selected)
        layout.addWidget(_bar(None, add))
        self.reload()

    def reload(self):
        self.history.load()
        self.table.setRowCount(0)
        for screen in self.history.recent(30):
            when = datetime.fromtimestamp(float(screen.get("at") or 0)).strftime("%b %d  %H:%M")
            for index, reward in enumerate(screen.get("rewards") or []):
                row = self.table.rowCount()
                self.table.insertRow(row)
                url = reward.get("url_name", "")
                if screen.get("picked") == url:
                    result = "Picked"
                elif screen.get("added") == url:
                    result = "Added by you"
                else:
                    result = ""
                price, ducats = reward.get("price"), reward.get("ducats")
                cells = [
                    when if index == 0 else "", reward.get("display_name", ""),
                    "—" if price is None else f"{price}p", "—" if ducats is None else f"{ducats}d", result,
                ]
                for column, text in enumerate(cells):
                    item = _table_item(text, mono=column in (2, 3))
                    item.setData(Qt.UserRole, (screen.get("id"), url, reward.get("display_name", "")))
                    self.table.setItem(row, column, item)
        if not self.history.screens:
            self.info.setText("No reward screens recorded yet. They appear here after your next Void Fissure run.")

    def add_selected(self):
        row = self.table.currentRow()
        item = self.table.item(row, 1) if row >= 0 else None
        if item is None:
            self._notify("Select the reward you took first.", "warning", 3000)
            return
        screen_id, url, name = item.data(Qt.UserRole)
        if not url:
            return
        self.store.increment_inventory_item(url, name, quantity=1)
        self.history.mark_added(screen_id, url)
        self.reload()
        self._notify(f"Added {name} to your Item List.", "success", 4000)

# ── page ────────────────────────────────────────────────────────────

class TradingToolsPage(QWidget):
    def __init__(self, store, get_api, notify):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.watchlist = WatchlistTab(get_api, notify)
        self.reprice = RepriceTab(get_api, notify)
        self.sales = SalesTab(store, notify)
        self.baro = BaroTab(get_api, notify)
        self.rewards = RewardHistoryTab(store, notify)
        self.tabs.addTab(self.watchlist, "Watchlist")
        self.tabs.addTab(self.reprice, "Repricing")
        self.tabs.addTab(self.sales, "Sales")
        self.tabs.addTab(self.baro, "Baro Ki'Teer")
        self.tabs.addTab(self.rewards, "Rewards")
        self.tabs.currentChanged.connect(lambda _i: self.on_shown())
        layout.addWidget(self.tabs)

    def on_shown(self):
        current = self.tabs.currentWidget()
        if current is self.watchlist:
            self.watchlist.on_shown()
        elif current is self.sales:
            self.sales.reload()
        elif current is self.baro:
            self.baro.on_shown()
        elif current is self.rewards:
            self.rewards.reload()


# ── Item List: set versus parts ─────────────────────────────────────

class SetCompareDialog(QDialog):
    """One line per set: set price, parts added up, and which way sells for more.

    Double-click a set to show or hide the price of each part. Prices are looked
    up one set at a time so the window fills in while it works.
    """

    row_ready = Signal(object, int, int)    # SetComparison or None, done, total

    def __init__(self, parent, api, catalog, set_slugs: list[str], owned: dict[str, int]):
        super().__init__(parent)
        self.setWindowTitle("Set vs parts")
        self._stop = False
        self._results: list = []
        self._expanded: set[int] = {0} if len(set_slugs) == 1 else set()
        layout = QVBoxLayout(self)
        self.heading = _muted(f"Looking up prices for {len(set_slugs)} set(s)...")
        layout.addWidget(self.heading)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["", "Set", "Set price", "Parts total", "Better to sell", "Gain"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        _prepare_table(self.table, [44, 300, 110, 120, 150, 100], stretch_column=1)
        self.table.doubleClicked.connect(self._toggle_parts)
        layout.addWidget(self.table, 1)
        self.total = QLabel("")
        self.total.setWordWrap(True)
        layout.addWidget(self.total)
        layout.addWidget(_muted("Press the arrow (or double-click a set) to show or hide the price of each part."))
        buttons = QHBoxLayout()
        expand = _button("Expand all", C_BG3, C_TEXT)
        expand.clicked.connect(lambda: self._set_all_expanded(True))
        collapse = _button("Collapse all", C_BG3, C_TEXT)
        collapse.clicked.connect(lambda: self._set_all_expanded(False))
        close = _button("Close", C_BLUE, "white")
        close.clicked.connect(self.accept)
        buttons.addWidget(expand)
        buttons.addWidget(collapse)
        buttons.addStretch()
        buttons.addWidget(close)
        layout.addLayout(buttons)
        # Wide enough for every column, short enough to leave the main window visible.
        main = parent.window() if parent is not None else None
        width = int(main.width() * 0.77) if main is not None else 1100
        height = int(main.height() * 0.71) if main is not None else 640
        self.resize(max(760, width), max(460, height))
        self.row_ready.connect(self._add_result)

        def work():
            total = len(set_slugs)
            for index, set_slug in enumerate(set_slugs, start=1):
                if self._stop:
                    return
                try:
                    result = trade_tools.compare_set_vs_parts(api, catalog, set_slug, owned)
                except Exception as e:
                    print(f"[Set compare] {set_slug}: {e}")
                    result = None
                self.row_ready.emit(result, index, total)

        _run_in_thread("Set comparison", work)

    def done(self, code):
        self._stop = True
        super().done(code)

    @staticmethod
    def _fmt(value) -> str:
        return "—" if value is None else f"{value}p"

    def _add_result(self, result, done: int, total: int):
        if result is not None:
            self._results.append(result)
            self._paint()
        self.heading.setText(
            f"Looked up {done} of {total} set(s)..." if done < total
            else f"{len(self._results)} set(s) compared. Prices are the lowest from players in game."
        )

    def _paint(self):
        self.table.setRowCount(0)
        gain_total = 0
        for index, result in enumerate(self._results):
            row = self.table.rowCount()
            self.table.insertRow(row)
            diff = None
            if result.set_price is not None and result.parts_total is not None:
                diff = result.set_price - result.parts_total
            better = "No data" if diff is None else ("Full set" if diff > 0 else "Parts" if diff < 0 else "Same")
            gain = "—" if diff is None else f"+{abs(diff)}p" if diff else "0p"
            if diff:
                gain_total += abs(diff)
            arrow = _button("▾" if index in self._expanded else "▸", C_BG3, C_TEXT)
            arrow.setToolTip("Show or hide the price of each part")
            arrow.clicked.connect(lambda _c=False, i=index: self._toggle_index(i))
            self.table.setCellWidget(row, 0, arrow)
            cells = [result.set_name, self._fmt(result.set_price), self._fmt(result.parts_total), better, gain]
            for column, text in enumerate(cells, start=1):
                item = _table_item(text, mono=column in (2, 3, 5))
                item.setData(Qt.UserRole, index)
                if column in (4, 5) and diff:
                    item.setForeground(QColor(C_GREEN if diff > 0 else C_BLUE))
                self.table.setItem(row, column, item)
            if index in self._expanded:
                parts = "   ·   ".join(
                    f"{p['name']} {self._fmt(p['price'])}" + (f" x{p['needed']}" if p["needed"] > 1 else "")
                    for p in result.parts
                )
                sub = self.table.rowCount()
                self.table.insertRow(sub)
                item = _table_item(parts)
                item.setForeground(QColor(C_MUTED))
                item.setData(Qt.UserRole, -1)
                self.table.setItem(sub, 1, item)
                self.table.setSpan(sub, 1, 1, 5)
        self.total.setText(
            f"Choosing the better option for every set earns {gain_total}p more than the worse one."
            if gain_total else ""
        )

    def _toggle_parts(self, index):
        item = self.table.item(index.row(), 1)
        key = item.data(Qt.UserRole) if item is not None else -1
        if key is None or key < 0:
            return
        self._toggle_index(key)

    def _toggle_index(self, key: int):
        self._expanded.symmetric_difference_update({key})
        self._paint()

    def _set_all_expanded(self, expanded: bool):
        self._expanded = set(range(len(self._results))) if expanded else set()
        self._paint()

# ── "was it sold?" prompt for manual removals ───────────────────────

class SoldPromptDialog(QDialog):
    """Asked when items are removed by hand: which of them were sold, and for how much?"""

    def __init__(self, parent, entries: list[tuple[str, str, int, int]]):
        super().__init__(parent)
        self.setWindowTitle("Was it sold?")
        self.setMinimumWidth(760)
        self._entries = entries
        self.sold: list[tuple[str, str, int, int]] = []
        layout = QVBoxLayout(self)
        layout.addWidget(_muted(
            "Tick what was sold, say how many, and enter the platinum you got for them (the total, not each). "
            "If you sold only some, the rest stays on your Item List. Leave the platinum at 0 if you don't remember."
        ))
        self.table = QTableWidget(len(entries), 4)
        self.table.setHorizontalHeaderLabels(["Sold", "Item", "How many", "Total platinum"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        _prepare_table(self.table, [70, 260, 170, 190], stretch_column=1)
        for row, (_slug, name, quantity, total) in enumerate(entries):
            box = _StyledCheckBox()
            box.setChecked(True)
            self.table.setCellWidget(row, 0, box)
            self.table.setItem(row, 1, _table_item(name))
            self.table.setRowHeight(row, 52)
            count = QSpinBox()
            count.setMinimumHeight(38)
            count.setRange(1, max(1, int(quantity)))
            count.setValue(max(1, int(quantity)))
            count.setSuffix(f" of {max(1, int(quantity))}")
            self.table.setCellWidget(row, 2, count)
            spin = QSpinBox()
            spin.setMinimumHeight(38)
            spin.setRange(0, 9_999_999)
            spin.setSuffix(" p")
            spin.setValue(max(0, int(total)))
            self.table.setCellWidget(row, 3, spin)
            unit = max(0, int(total)) / max(1, int(quantity))
            # Keep the price in step with the count: 1 of 2 sold = half the total.
            count.valueChanged.connect(lambda value, s=spin, u=unit: s.setValue(round(u * value)))
        layout.addWidget(self.table)
        self.table.setMinimumHeight(min(80 + 54 * len(entries), 380))
        buttons = QHBoxLayout()
        no = _button("No, not sold", C_BG3, C_TEXT)
        yes = _button("Yes, add to Sales", C_GREEN, "white")
        no.clicked.connect(self.reject)
        yes.clicked.connect(self._accept)
        buttons.addStretch()
        buttons.addWidget(no)
        buttons.addWidget(yes)
        layout.addLayout(buttons)

    def _accept(self):
        self.sold = []
        for row, (slug, name, quantity, _total) in enumerate(self._entries):
            box = self.table.cellWidget(row, 0)
            if isinstance(box, _StyledCheckBox) and box.isChecked():
                self.sold.append((
                    slug, name, int(self.table.cellWidget(row, 2).value()),
                    int(self.table.cellWidget(row, 3).value()),
                ))
        self.accept()


def ask_sold(parent, entries: list[tuple[str, str, int, int]]) -> list[tuple[str, str, int, int]]:
    """Ask which removed items were sold. Returns (slug, name, quantity, total plat) for those that were."""
    if not entries:
        return []
    dialog = SoldPromptDialog(parent, entries)
    if dialog.exec() != QDialog.Accepted:
        return []
    return dialog.sold