"""Public GitHub release discovery and verified installer downloads.

Finds the newest stable Parallel release, checks that its installer asset comes
from this repository with a published checksum, and validates the download
before the UI offers to run it.
"""
from datetime import date
import hashlib
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

REPOSITORY = "Chukway/Parallel"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
ASSET_NAME = "Parallel-Setup.exe"
MAX_DOWNLOAD = 1024 * 1024 * 1024
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "Parallel-Updater", "X-GitHub-Api-Version": "2026-03-10"}
# Quiet install over the existing copy: the old program is closed first and the
# installer's own run step starts the new one.
INSTALLER_ARGS = ("/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS")


def automatic_check_due(frequency, last_checked, today=None):
    """Successful checks postpone the next startup check by the chosen interval."""
    if frequency == "never":
        return False
    interval = {"daily": 1, "weekly": 7, "monthly": 30}.get(frequency, 1)
    today = today or date.today()
    try:
        previous = date.fromisoformat(last_checked)
    except (TypeError, ValueError):
        return True
    return previous > today or (today - previous).days >= interval


def version(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(value))
    if not match:
        raise ValueError("Release versions must use vMAJOR.MINOR.PATCH.")
    return tuple(int(part) for part in match.groups())


def check_release(installed):
    with requests.get(f"https://api.github.com/repos/{REPOSITORY}/releases/latest", headers=HEADERS, timeout=(5, 10)) as response:
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    if version(data.get("tag_name")) <= version(installed):
        return None
    tag = data["tag_name"]
    page = f"{RELEASES_URL}/tag/{tag}"
    asset = next((item for item in data.get("assets", []) if item.get("name") == ASSET_NAME), None)
    result = {"version": tag, "page": page, "asset": None}
    if asset:
        url = asset.get("browser_download_url", "")
        digest = asset.get("digest", "")
        expected = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{asset['name']}"
        if url == expected and isinstance(digest, str) and re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest):
            size = asset.get("size")
            if type(size) is int and 0 < size <= MAX_DOWNLOAD:
                result["asset"] = {"url": url, "sha256": digest[7:].lower(), "size": size}
    return result


def installer_command(installer):
    """The command line that installs a downloaded release quietly over this copy."""
    return [str(installer), *INSTALLER_ARGS]


def verify_download(path, expected):
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError("Invalid update checksum.")
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise ValueError("The update download failed its integrity check.")


def download_release(asset, destination, progress=lambda _percent: None, cancelled=lambda: False):
    received = 0
    started = time.monotonic()
    try:
        with requests.get(asset["url"], headers={"User-Agent": HEADERS["User-Agent"]}, timeout=(5, 30), stream=True) as response:
            response.raise_for_status()
            parsed = urlparse(response.url)
            if parsed.scheme != "https" or parsed.hostname not in ("github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"):
                raise ValueError("Unexpected update download host.")
            with Path(destination).open("wb") as stream:
                for chunk in response.iter_content(256 * 1024):
                    if cancelled():
                        raise InterruptedError("Update download cancelled.")
                    if time.monotonic() - started > 15 * 60:
                        raise TimeoutError("Update download timed out.")
                    received += len(chunk)
                    if received > asset["size"] or received > MAX_DOWNLOAD:
                        raise ValueError("Update exceeds its published size.")
                    stream.write(chunk)
                    progress(min(99, received * 100 // asset["size"]))
        if received != asset["size"]:
            raise ValueError("Update download is incomplete.")
        verify_download(destination, asset["sha256"])
    except Exception:
        Path(destination).unlink(missing_ok=True)
        raise
