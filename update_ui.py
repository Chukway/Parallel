"""Background update checks and download/install controls for Parallel.

Connects update_core.py to startup and Settings, persists local update
preferences and starts the separate updater without blocking the UI.
"""
from datetime import date
import json
from pathlib import Path
import subprocess
import tempfile
import threading

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

import config
from update_core import ASSET_NAME, automatic_check_due, check_release, download_release, installer_command


def installed_by_setup() -> bool:
    """True when this copy came from Parallel-Setup.exe, so the same installer can update it."""
    return config.IS_FROZEN and (config.APP_DIR / 'unins000.exe').is_file()


class UpdateController:
    def __init__(self, ui, is_busy):
        self.ui = ui
        self.is_busy = is_busy
        self.busy = False
        self.progress = None
        self.cancelled = threading.Event()
        self.check_date_path = config.USER_DATA_DIR / 'update_check.json'
        ui.app.parallel_updates = self
        QTimer.singleShot(6000, self.check_automatically)

    def parent(self):
        return QApplication.activeModalWidget() or self.ui.dashboard

    def tasks_active(self):
        return self.is_busy()

    def check_automatically(self):
        if not getattr(config, 'UPDATE_SOURCE_ENABLED', False):
            return
        last_checked = None
        try:
            cached = json.loads(self.check_date_path.read_text(encoding='utf-8'))
            last_checked = cached.get('date')
        except (OSError, ValueError, AttributeError):
            pass
        if automatic_check_due(config.UPDATE_CHECK_FREQUENCY, last_checked):
            self.check(manual=False)

    def check(self, manual=True):
        if not getattr(config, 'UPDATE_SOURCE_ENABLED', False):
            if manual:
                QMessageBox.information(self.parent(), f'{config.APP_NAME} updates', f'Update checks are turned off for this build ({config.APP_NAME} {config.APP_VERSION}).')
            return
        if self.busy:
            if manual:
                QMessageBox.information(self.parent(), 'Parallel updates', 'An update check or download is already running.')
            return
        self.busy = True

        def worker():
            try:
                release = check_release(config.APP_VERSION)
                try:
                    self.check_date_path.write_text(json.dumps({'date': date.today().isoformat()}), encoding='utf-8')
                except OSError:
                    pass
                self.ui.invoke(lambda: self.checked(release, manual))
            except Exception as exc:
                self.ui.invoke(lambda error=str(exc): self.check_failed(error, manual))
        threading.Thread(target=worker, name='Parallel: Update check', daemon=True).start()

    def check_failed(self, error, manual):
        self.busy = False
        if manual:
            QMessageBox.warning(self.parent(), 'Parallel updates', f'Could not check for updates. Check your connection and try again.\n\n{error}')

    def checked(self, release, manual):
        self.busy = False
        if release is None:
            if manual:
                QMessageBox.information(self.parent(), 'Parallel updates', f'Parallel {config.APP_VERSION}: no newer stable release is available.')
            return
        dialog = QMessageBox(self.parent())
        dialog.setWindowTitle('Parallel update available')
        dialog.setText(f"Parallel {release['version']} is available.\nInstalled: {config.APP_VERSION}")
        available = release['asset'] is not None and installed_by_setup()
        install = dialog.addButton('Update now', QMessageBox.AcceptRole) if available else None
        if available:
            dialog.setInformativeText('Download the update, then restart Parallel to install it. Your settings and saved sign-ins are preserved.')
        else:
            dialog.setInformativeText(f'Open the release page and download {ASSET_NAME}. Automatic installation only works for copies installed with it.')
        notes = dialog.addButton('Release notes', QMessageBox.ActionRole)
        later = dialog.addButton('Later', QMessageBox.RejectRole)
        dialog.setDefaultButton(later)
        dialog.exec()
        if dialog.clickedButton() == notes:
            QDesktopServices.openUrl(QUrl(release['page']))
        elif install is not None and dialog.clickedButton() == install:
            self.download(release)

    def download(self, release):
        if self.tasks_active():
            QMessageBox.information(self.parent(), 'Parallel updates', 'Finish active tasks and turn off live scanning before updating Parallel.')
            return
        self.busy = True
        self.cancelled.clear()
        self.progress = QProgressDialog('Downloading and verifying the Parallel update…', 'Cancel', 0, 100, self.parent())
        self.progress.setWindowTitle('Parallel update')
        self.progress.setWindowModality(Qt.ApplicationModal)
        self.progress.setAutoClose(False)
        self.progress.setAutoReset(False)
        self.progress.canceled.connect(self.cancelled.set)
        self.progress.show()

        def worker():
            work = None
            try:
                updates = config.USER_DATA_DIR / 'updates'
                updates.mkdir(parents=True, exist_ok=True)
                work = Path(tempfile.mkdtemp(prefix='update-', dir=updates))
                download_release(release['asset'], work / ASSET_NAME,
                    progress=lambda percent: self.ui.invoke(lambda value=percent: self.progress.setValue(value)),
                    cancelled=self.cancelled.is_set)
                if self.cancelled.is_set():
                    raise InterruptedError('Update download cancelled.')
                self.ui.invoke(lambda folder=work: self.ready(release, folder))
            except Exception as exc:
                self.ui.invoke(lambda error=str(exc): self.download_failed(error))
        threading.Thread(target=worker, name='Parallel: Update download', daemon=True).start()

    def download_failed(self, error):
        self.busy = False
        self.progress.blockSignals(True)
        self.progress.close()
        if not self.cancelled.is_set():
            QMessageBox.warning(self.parent(), 'Parallel updates', f'The update was not installed. Parallel will keep running.\n\n{error}')

    def ready(self, release, work):
        self.progress.blockSignals(True)
        self.progress.close()
        self.busy = False
        if self.cancelled.is_set():
            return
        if self.tasks_active():
            QMessageBox.information(self.parent(), 'Parallel updates', 'A task started during download. Finish it and check for updates again when ready.')
            return
        answer = QMessageBox.question(self.parent(), 'Restart to update Parallel',
            f"Parallel {release['version']} is downloaded and verified.\nClose Parallel and install it now?\nUnsaved changes will not be saved.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        try:
            subprocess.Popen(installer_command(work / ASSET_NAME), cwd=work)
        except Exception as exc:
            QMessageBox.warning(self.parent(), 'Parallel updates', f'Could not start the installer. Parallel will keep running.\n\n{exc}')
            return
        self.ui.stop()


def check_for_updates():
    controller = getattr(QApplication.instance(), 'parallel_updates', None)
    if controller is not None:
        controller.check(manual=True)
