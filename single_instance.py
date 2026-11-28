"""Allow only one running Parallel for each data folder.

Two copies sharing the same files each keep the Item List in memory and write it
back, so one silently undoes the other's changes. The first copy holds a named
Windows mutex. A later launch asks the first one to bring its window forward and
then exits. The names are tied to the data folder, so a test copy that uses its
own folder can still run beside the normal one.
"""

import ctypes
import hashlib
import os
import threading
from ctypes import wintypes

_ERROR_ALREADY_EXISTS = 183
_WAIT_OBJECT_0 = 0


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateEventW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
    kernel32.ResetEvent.argtypes = (wintypes.HANDLE,)
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    return kernel32


class SingleInstance:
    """Create one at startup; ``is_first`` says whether this process may continue."""

    def __init__(self, data_dir):
        self.is_first = True
        self._handles: list = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._kernel32 = None
        if os.name != "nt":
            return
        key = hashlib.sha256(os.path.normcase(str(data_dir)).encode("utf-8")).hexdigest()[:20]
        base = f"Local\\Parallel-{key}"
        self._kernel32 = _kernel32()
        mutex = self._kernel32.CreateMutexW(None, False, base + "-running")
        error = ctypes.get_last_error()
        if not mutex:
            return          # Windows refused the check itself; never block the app over that
        self._handles.append(mutex)
        self.is_first = error != _ERROR_ALREADY_EXISTS
        self._show = self._kernel32.CreateEventW(None, False, False, base + "-show")
        self._shown = self._kernel32.CreateEventW(None, False, False, base + "-shown")
        self._handles += [h for h in (self._show, self._shown) if h]

    def listen(self, on_show) -> None:
        """In the first copy: call ``on_show`` whenever another launch asks for the window."""
        if self._kernel32 is None or not self.is_first or self._thread is not None:
            return

        def wait():
            while not self._stop.is_set():
                if self._kernel32.WaitForSingleObject(self._show, 300) != _WAIT_OBJECT_0:
                    continue
                try:
                    on_show()
                except Exception as e:
                    print(f"[Main] Could not bring the window forward: {e}")
                    continue
                self._kernel32.SetEvent(self._shown)

        self._thread = threading.Thread(target=wait, name="Parallel: Single instance", daemon=True)
        self._thread.start()

    def ask_first_to_show(self, timeout: float = 10.0) -> bool:
        """In a later copy: ask the running one for its window. False if it did not answer."""
        if self._kernel32 is None:
            return False
        try:
            # This process was just started by the user, so it may hand over the foreground.
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
        except Exception:
            pass
        self._kernel32.ResetEvent(self._shown)
        self._kernel32.SetEvent(self._show)
        return self._kernel32.WaitForSingleObject(self._shown, int(timeout * 1000)) == _WAIT_OBJECT_0

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        for handle in self._handles:
            self._kernel32.CloseHandle(handle)
        self._handles = []


def show_already_running_message(app_name: str) -> None:
    ctypes.windll.user32.MessageBoxW(
        None,
        f"{app_name} is already running but did not answer.\n\n"
        f"Look for its icon in the system tray. If it is stuck, end {app_name}.exe in Task Manager "
        "and open it again.",
        app_name,
        0x40,
    )
