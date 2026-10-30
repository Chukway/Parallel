"""Backups of everything Parallel remembers: items, listings, sales, lists and settings.

A backup is one zip of the small JSON files in the data folder. The market token
(``.env``) is left out unless asked for, because a backup file is easy to share by
accident. Restoring first saves a safety copy of the current data.
"""

from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path

import config

# Only these files are ever written or read; anything else in a zip is ignored.
DATA_FILES = (
    "account_state.json",   # items you own, your listings, queued rewards, sales log
    "mastery_list.json",
    "arcane_stock.json",
    "app_settings.json",
)
TOKEN_FILE = ".env"
MANIFEST = "backup_info.json"
MAX_FILE_BYTES = 50 * 1024 * 1024
AUTO_KEEP = 10
AUTO_INTERVAL_SECONDS = 20 * 3600


def backups_dir() -> Path:
    path = Path(config.USER_DATA_DIR) / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_backup(destination: Path, include_token: bool = False, data_dir: Path | None = None) -> Path:
    data_dir = Path(data_dir or config.USER_DATA_DIR)
    names = list(DATA_FILES) + ([TOKEN_FILE] if include_token else [])
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stored = []
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            source = data_dir / name
            if source.is_file():
                archive.write(source, name)
                stored.append(name)
        archive.writestr(MANIFEST, json.dumps({
            "app": config.APP_NAME,
            "version": config.APP_VERSION,
            "created_at": time.time(),
            "files": stored,
            "includes_token": TOKEN_FILE in stored,
        }, indent=2))
    return destination


def read_backup_info(archive_path: Path) -> dict:
    with zipfile.ZipFile(archive_path) as archive:
        try:
            info = json.loads(archive.read(MANIFEST).decode("utf-8"))
        except (KeyError, ValueError):
            raise ValueError("This is not a Parallel backup.")
    if not isinstance(info, dict) or info.get("app") not in {config.APP_NAME, "Parallel", "Sidro"}:
        raise ValueError("This is not a Parallel backup.")
    return info


def restore_backup(archive_path: Path, data_dir: Path | None = None) -> list[str]:
    """Restore the known files from a backup. Returns the names restored."""
    data_dir = Path(data_dir or config.USER_DATA_DIR)
    read_backup_info(archive_path)
    safety = data_dir / "backups" / f"before-restore-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    create_backup(safety, include_token=True, data_dir=data_dir)
    restored: list[str] = []
    allowed = set(DATA_FILES) | {TOKEN_FILE}
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            if info.filename not in allowed:
                continue  # also rejects paths such as ../x or sub/folder
            if info.file_size > MAX_FILE_BYTES:
                raise ValueError(f"{info.filename} is too large to be a real backup file.")
            payload = archive.read(info)
            if info.filename.endswith(".json"):
                json.loads(payload.decode("utf-8"))  # refuse damaged files before touching disk
            target = data_dir / info.filename
            temporary = target.with_suffix(target.suffix + ".restore")
            temporary.write_bytes(payload)
            temporary.replace(target)
            restored.append(info.filename)
    return restored


def auto_backup(data_dir: Path | None = None) -> Path | None:
    """Make a rolling backup at most about once a day. Never raises."""
    try:
        folder = backups_dir() if data_dir is None else Path(data_dir) / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        existing = sorted(folder.glob("auto-*.zip"))
        if existing and time.time() - existing[-1].stat().st_mtime < AUTO_INTERVAL_SECONDS:
            return None
        source = Path(data_dir or config.USER_DATA_DIR)
        if not (source / "account_state.json").is_file():
            return None
        made = create_backup(folder / f"auto-{time.strftime('%Y%m%d-%H%M%S')}.zip", data_dir=source)
        for old in sorted(folder.glob("auto-*.zip"))[:-AUTO_KEEP]:
            old.unlink(missing_ok=True)
        return made
    except Exception as exc:  # a failed backup must never stop the app
        print(f"[Backup] automatic backup failed: {exc}")
        return None
