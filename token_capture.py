"""Helpers for getting a warframe.market token without hand-copying headers.

Two routes, both user-driven (the user signs in themselves):

* TokenCaptureDialog: in-app sign-in window (Qt WebEngine, imported lazily,
  dedicated app-owned profile). Reads ONLY the ``JWT`` cookie for
  warframe.market from that profile.
* SmartCopyDialog: no extra install. Opens the sign-in page in the default
  browser and watches the clipboard for a JWT-looking string.

Nothing here logs or prints a token, stores a password, or reads any other
browser's data.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import threading
from pathlib import Path
from typing import Callable, Iterable

import auth_manager

SIGNIN_URL = "https://warframe.market/auth/signin"
COOKIE_NAME = "JWT"
COOKIE_DOMAIN = "warframe.market"

_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")

CONSOLE_ONE_LINER = (
    "copy(document.cookie.match(/(?:^|; )JWT=([^;]+)/)?.[1] "
    "|| 'JWT cookie is hidden from scripts')"
)


# ---------------------------------------------------------------- detection

def find_jwt(text: str | None) -> str | None:
    """Return a JWT-looking token found in ``text`` or None."""
    if not text or len(text) > 8192:
        return None
    match = _JWT_RE.search(auth_manager._clean_token(text))
    return match.group(0) if match else None


def domain_matches(cookie_domain: str, wanted: str = COOKIE_DOMAIN) -> bool:
    d = (cookie_domain or "").lstrip(".").lower()
    return d == wanted or d.endswith("." + wanted)


def _as_str(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "ignore")
    if hasattr(value, "data"):  # QByteArray
        try:
            return bytes(value.data()).decode("utf-8", "ignore")
        except Exception:
            pass
    return str(value)


def pick_jwt_cookie(cookies: Iterable, domain: str = COOKIE_DOMAIN) -> str | None:
    """From QNetworkCookie-like objects (name()/domain()/value()) return the
    JWT value of the warframe.market ``JWT`` cookie, ignoring all others."""
    for c in cookies:
        if _as_str(c.name()) != COOKIE_NAME:
            continue
        if not domain_matches(_as_str(c.domain()), domain):
            continue
        token = find_jwt(_as_str(c.value()))
        if token:
            return token
    return None


# ---------------------------------------------------------- clipboard watch

class ClipboardWatcher:
    """Calls ``on_token`` once per new JWT seen on the clipboard.

    ``clipboard`` only needs a ``text()`` method. Call ``check()`` from the
    clipboard's change signal or a timer; ``stop()`` makes it inert.
    """

    def __init__(self, clipboard, on_token: Callable[[str], None]):
        self._clipboard = clipboard
        self._on_token = on_token
        self._last: str | None = None
        self._active = True

    def check(self) -> str | None:
        if not self._active:
            return None
        try:
            token = find_jwt(self._clipboard.text())
        except Exception:
            return None
        if token and token != self._last:
            self._last = token
            self._on_token(token)
            return token
        return None

    def stop(self) -> None:
        self._active = False


# ------------------------------------------------------------- save helper

def validate_and_save(token: str, env_path: Path | None = None) -> tuple[bool, str]:
    """Validate then save. Returns (ok, message); message never has the token."""
    token = find_jwt(token) or ""
    if not token:
        return False, "That does not look like a warframe.market token."
    try:
        valid = auth_manager.validate_wfm_token(token)
    except Exception:
        return False, "Could not reach warframe.market to check the token."
    if not valid:
        return False, "warframe.market rejected this token. Sign in again."
    try:
        auth_manager.save_wfm_token(token, env_path)
    except Exception:
        return False, "The token was valid but could not be saved."
    return True, "Token saved."


# ------------------------------------------------------------------ WebEngine

def webengine_available() -> bool:
    """True when Qt WebEngine can be imported. Does not import it."""
    try:
        return (importlib.util.find_spec("PySide6.QtWebEngineWidgets") is not None
                and importlib.util.find_spec("PySide6.QtWebEngineCore") is not None)
    except Exception:
        return False


def load_webengine():
    """Lazy import. Returns (QWebEngineView, QWebEngineProfile, QWebEnginePage)
    or None when WebEngine is missing or fails to import."""
    try:
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
        from PySide6.QtWebEngineWidgets import QWebEngineView
    except Exception as exc:
        print(f"[TokenCapture] Qt WebEngine could not be loaded: {exc!r}")
        return None
    return QWebEngineView, QWebEngineProfile, QWebEnginePage


def default_profile_dir() -> Path:
    import config
    return Path(config.ENV_PATH).parent / "webprofile"


# --------------------------------------------------------------------- UI

def TokenCaptureDialog(parent=None, on_success: Callable[[], None] | None = None,
                       profile_dir: Path | None = None):
    """Factory (keeps WebEngine import lazy). Returns a QDialog, or None when
    WebEngine is not installed; callers should then use SmartCopyDialog."""
    mods = load_webengine()
    if mods is None:
        return None
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

    QWebEngineView, QWebEngineProfile, QWebEnginePage = mods

    class _Dialog(QDialog):
        _result = Signal(bool, str)

        def __init__(self):
            super().__init__(parent)
            self.setWindowTitle("Sign in to warframe.market")
            self.resize(520, 720)
            self._done = False
            self._busy = False
            lay = QVBoxLayout(self)
            self._status = QLabel(
                "Sign in below. The window closes by itself when you are done. "
                "Your password is never seen or saved by Parallel.")
            self._status.setWordWrap(True)
            lay.addWidget(self._status)
            # A profile without a name is off the record: the sign-in session lives in memory
            # only, so no logged-in browser session is left on disk once the window closes.
            # Older versions stored one in "webprofile"; remove that leftover.
            shutil.rmtree(Path(profile_dir or default_profile_dir()), ignore_errors=True)
            self._profile = QWebEngineProfile(self)
            self._page = QWebEnginePage(self._profile, self)
            self._view = QWebEngineView(self)
            self._view.setPage(self._page)
            lay.addWidget(self._view, 1)
            self._result.connect(self._on_result)
            store = self._profile.cookieStore()
            store.cookieAdded.connect(self._cookie_seen)
            self._view.loadFinished.connect(self._page_loaded)
            self._seen_names: set[str] = set()
            self._view.load(SIGNIN_URL)

        def _page_loaded(self, ok: bool):
            self._profile.cookieStore().loadAllCookies()
            url = self._view.url()
            host = url.host()
            print(f"[TokenCapture] page loaded ok={ok} host={host} path={url.path()} "
                  f"cookie names seen={sorted(self._seen_names)}")
            if self._done or self._busy or not domain_matches(host):
                return
            # Fallback: some sites keep the token in page storage instead of a cookie.
            script = ("(function(){var f=function(s){for(var i=0;i<s.length;i++){var v=s.getItem(s.key(i));"
                      "if(v&&/eyJ[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+\\.[A-Za-z0-9_-]+/.test(v))return v;}return '';};"
                      "try{return f(localStorage)||f(sessionStorage);}catch(e){return '';}})()")
            self._page.runJavaScript(script, self._storage_result)

        def _storage_result(self, value):
            token = find_jwt(value if isinstance(value, str) else "")
            if token:
                print("[TokenCapture] token found in page storage")
                self._accept_token(token)

        def _cookie_seen(self, cookie):
            # Only the warframe.market JWT cookie is ever looked at.
            if self._done or self._busy:
                return
            try:
                if domain_matches(_as_str(cookie.domain())):
                    self._seen_names.add(_as_str(cookie.name()))   # names only, never values
            except Exception:
                pass
            token = pick_jwt_cookie([cookie])
            if not token:
                return
            print("[TokenCapture] JWT cookie found")
            self._accept_token(token)

        def _accept_token(self, token: str):
            if self._done or self._busy:
                return
            self._busy = True
            self._status.setText("Checking your sign-in...")

            def work():
                ok, msg = validate_and_save(token)
                self._result.emit(ok, msg)

            threading.Thread(target=work, daemon=True).start()

        def _on_result(self, ok: bool, msg: str):
            self._busy = False
            print(f"[TokenCapture] result ok={ok}: {msg}")
            if not ok and "rejected" in msg:
                # The site sets a guest cookie before sign-in; keep waiting for the real one.
                msg = "Sign in below. The window closes by itself when you are done."
            self._status.setText(msg)
            if ok:
                self._done = True
                if on_success:
                    on_success()
                self.accept()

        def done(self, r):
            self._done = True
            try:
                self._profile.cookieStore().cookieAdded.disconnect(self._cookie_seen)
            except Exception:
                pass
            self._view.setPage(None)
            super().done(r)

    return _Dialog()


def SmartCopyDialog(parent=None, on_success: Callable[[], None] | None = None,
                    open_url: Callable[[str], object] | None = None):
    """Fallback dialog, no extra install. Watches the clipboard while open."""
    from PySide6.QtCore import Qt, QUrl, Signal
    from PySide6.QtGui import QDesktopServices, QGuiApplication
    from PySide6.QtWidgets import (QDialog, QLabel, QPushButton, QVBoxLayout)

    class _Dialog(QDialog):
        _result = Signal(bool, str)

        def __init__(self):
            super().__init__(parent)
            self.setWindowTitle("Get your token (smart copy)")
            self.resize(520, 480)
            self._token: str | None = None
            lay = QVBoxLayout(self)
            steps = QLabel(
                "<b>1.</b> Click the button below and sign in to warframe.market.<br>"
                "<b>2.</b> Press <b>F12</b>, open <b>Application</b> (or Storage), "
                "then <b>Cookies</b> &rarr; warframe.market, and find the row "
                "named <b>JWT</b>.<br>"
                "<b>3.</b> Double-click its Value and copy it (Ctrl+C). "
                "This window notices it automatically."
                "<br><br><small>Optional shortcut: on the warframe.market tab press F12, "
                "open <b>Console</b>, paste the line below and press Enter. If it says "
                "the cookie is hidden, use step 2 instead.</small>")
            steps.setWordWrap(True)
            lay.addWidget(steps)
            self._snippet = QLabel(CONSOLE_ONE_LINER)
            self._snippet.setWordWrap(True)
            self._snippet.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lay.addWidget(self._snippet)
            self._open = QPushButton("Open warframe.market sign-in")
            self._open.clicked.connect(self._open_site)
            lay.addWidget(self._open)
            self._status = QLabel("Waiting for you to copy the token...")
            self._status.setWordWrap(True)
            lay.addWidget(self._status)
            self._use = QPushButton("Use this token")
            self._use.setEnabled(False)
            self._use.clicked.connect(self._use_token)
            lay.addWidget(self._use)
            self._cb = QGuiApplication.clipboard()
            self._watcher = ClipboardWatcher(self._cb, self._found)
            self._cb.dataChanged.connect(self._watcher.check)
            self._result.connect(self._on_result)
            self._watcher.check()

        def _open_site(self):
            (open_url or (lambda u: QDesktopServices.openUrl(QUrl(u))))(SIGNIN_URL)

        def _found(self, token: str):
            self._token = token
            self._status.setText("Token found on your clipboard.")
            self._use.setEnabled(True)

        def _use_token(self):
            if not self._token:
                return
            token = self._token
            self._use.setEnabled(False)
            self._status.setText("Checking your token...")
            threading.Thread(
                target=lambda: self._result.emit(*validate_and_save(token)),
                daemon=True).start()

        def _on_result(self, ok: bool, msg: str):
            self._status.setText(msg)
            if ok:
                try:
                    self._cb.clear()  # do not leave the token on the clipboard
                except Exception:
                    pass
                if on_success:
                    on_success()
                self.accept()
            else:
                self._use.setEnabled(self._token is not None)

        def done(self, r):
            self._watcher.stop()
            try:
                self._cb.dataChanged.disconnect(self._watcher.check)
            except Exception:
                pass
            super().done(r)

    return _Dialog()


def open_best_dialog(parent=None, on_success: Callable[[], None] | None = None):
    """WebEngine window when available, otherwise the smart-copy dialog."""
    return TokenCaptureDialog(parent, on_success) or SmartCopyDialog(parent, on_success)
