"""A small always-on-top pill that shows a task's progress as lit dots.

The pill never takes focus and ignores the mouse, so it can sit over Warframe
(borderless or windowed fullscreen) while a Void Fissure is running. Each step
of the task gets one dot: grey while waiting, green when done, red on a problem.
"""

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

PENDING = "pending"
DONE = "done"
FAILED = "failed"
STATES = (PENDING, DONE, FAILED)

CORNERS = ("top_left", "top_right", "bottom_left", "bottom_right")

# One entry per tracked task: pill title and the label of each dot, in order.
OVERLAY_SPECS = {
    "fissure": ("FISSURE", ("Read", "Picked", "Confirmed", "Queued")),
    "trade": ("TRADE", ("Open", "Item found", "Completed", "Logged")),
}

_DOT = 10
_PAD = 10
_GAP = 10
_HEIGHT = 28
_NOTE_HEIGHT = 17
_MARGIN = 14

_COLORS = {
    PENDING: QColor(107, 114, 128),
    DONE: QColor(61, 220, 132),
    FAILED: QColor(229, 83, 75),
}


def clean_corner(value) -> str:
    return value if value in CORNERS else "top_right"


class StepOverlay(QWidget):
    """The pill itself. All methods must be called on the Qt thread."""

    def __init__(self, title: str, steps, corner: str = "top_right",
                 show_labels: bool = True, hide_after: int = 0):
        flags = Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        try:
            flags |= Qt.WindowTransparentForInput
        except AttributeError:
            pass
        super().__init__(None, flags)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.title = str(title)
        self.steps = tuple(str(step) for step in steps)
        self.states = [PENDING] * len(self.steps)
        self.note = ""
        self.corner = clean_corner(corner)
        self.show_labels = bool(show_labels)
        self.hide_after = max(0, int(hide_after))
        self._active = False
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self._relayout()

    # ── state ─────────────────────────────────────────────
    def set_state(self, index: int, state: str = DONE, note: str = "") -> None:
        if not 0 <= index < len(self.states) or state not in STATES:
            return
        if state == DONE and self.states[index] == DONE:
            return  # already lit: repeated polls must not rewrite the caption
        self.states[index] = state
        self.note = str(note)
        # A step can only be lit once the steps before it are done.
        if state == DONE:
            for earlier in range(index):
                if self.states[earlier] == PENDING:
                    self.states[earlier] = DONE
        else:
            for later in range(index + 1, len(self.states)):
                self.states[later] = PENDING
        self._relayout()
        self.update()
        self._wake()

    def reset(self) -> None:
        if not self.note and all(state == PENDING for state in self.states):
            return
        self.states = [PENDING] * len(self.steps)
        self.note = ""
        self._relayout()
        self.update()

    def set_active(self, active: bool) -> None:
        """Active means its feature is switched on; only then may the pill show."""
        self._active = bool(active)
        if not self._active:
            self._hide_timer.stop()
            self.hide()
        elif self.hide_after == 0:
            self._relayout()
            self.show()

    def apply_options(self, corner: str, show_labels: bool, hide_after: int) -> None:
        self.corner = clean_corner(corner)
        self.show_labels = bool(show_labels)
        self.hide_after = max(0, int(hide_after))
        self._relayout()
        self.update()
        if self._active and self.hide_after == 0:
            self.show()
        elif self.hide_after:
            self._hide_timer.stop()
            self.hide()

    def _wake(self) -> None:
        if not self._active:
            return
        self.show()
        if self.hide_after:
            self._hide_timer.start(self.hide_after * 1000)

    # ── geometry ──────────────────────────────────────────
    def _font(self, bold: bool = False, size: int = 9) -> QFont:
        font = QFont(QApplication.font())
        font.setPointSize(size)
        font.setBold(bold)
        return font

    def _content_width(self) -> int:
        title_w = QFontMetrics(self._font(True, 8)).horizontalAdvance(self.title)
        small = QFontMetrics(self._font())
        width = _PAD + title_w + _GAP
        for step in self.steps:
            width += _DOT + _GAP
            if self.show_labels:
                width += 4 + small.horizontalAdvance(step)
        return width + _PAD - _GAP

    def _relayout(self) -> None:
        width = max(self._content_width(), 190 if self.show_labels else 0)
        height = _HEIGHT + (_NOTE_HEIGHT if self.note else 0)
        self.setFixedSize(width, height)
        self.place()

    def place(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = area.left() + _MARGIN if "left" in self.corner else area.right() - self.width() - _MARGIN
        y = area.top() + 4 if "top" in self.corner else area.bottom() - self.height() - _MARGIN
        self.move(x, y)

    # ── drawing ───────────────────────────────────────────
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        painter.setBrush(QColor(14, 16, 24, 218))
        painter.setPen(QPen(QColor(200, 169, 110, 150), 1))
        painter.drawRoundedRect(rect, 9, 9)

        painter.setFont(self._font(True, 8))
        painter.setPen(QColor(200, 169, 110))
        title_w = QFontMetrics(painter.font()).horizontalAdvance(self.title)
        painter.drawText(QRectF(_PAD, 0, title_w, _HEIGHT), Qt.AlignVCenter | Qt.AlignLeft, self.title)

        small = QFontMetrics(self._font())
        x = _PAD + title_w + _GAP
        cy = _HEIGHT / 2
        painter.setFont(self._font())
        for step, state in zip(self.steps, self.states):
            color = _COLORS[state]
            if state == DONE:
                glow = QColor(color)
                glow.setAlpha(70)
                painter.setPen(Qt.NoPen)
                painter.setBrush(glow)
                painter.drawEllipse(QRectF(x - 3, cy - _DOT / 2 - 3, _DOT + 6, _DOT + 6))
            if state == PENDING:
                painter.setBrush(Qt.NoBrush)
                painter.setPen(QPen(color, 1.5))
            else:
                painter.setBrush(color)
                painter.setPen(Qt.NoPen)
            painter.drawEllipse(QRectF(x, cy - _DOT / 2, _DOT, _DOT))
            x += _DOT
            if self.show_labels:
                x += 4
                label_w = small.horizontalAdvance(step)
                painter.setPen(QColor(226, 228, 235) if state != PENDING else QColor(130, 136, 150))
                painter.drawText(QRectF(x, 0, label_w, _HEIGHT), Qt.AlignVCenter | Qt.AlignLeft, step)
                x += label_w
            x += _GAP

        if self.note:
            painter.setFont(self._font(False, 8))
            painter.setPen(QColor(255, 140, 130) if FAILED in self.states else QColor(170, 176, 190))
            text = QFontMetrics(painter.font()).elidedText(self.note, Qt.ElideRight, self.width() - 2 * _PAD)
            painter.drawText(QRectF(_PAD, _HEIGHT - 4, self.width() - 2 * _PAD, _NOTE_HEIGHT),
                             Qt.AlignVCenter | Qt.AlignLeft, text)
        painter.end()
