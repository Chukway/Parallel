"""Scan the project for personal data before publishing it (for example to GitHub).

Run:  python tools/audit_personal_data.py [--extra TERM ...]

It looks for e-mail addresses, absolute user paths, token-like strings, the
local user name and any extra terms you pass (your name, handle, address...).
Exit code 1 means something was found. Nothing is modified.
"""
from __future__ import annotations

import argparse
import getpass
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".venv-sidro", ".venv", "venv", "__pycache__", ".git", "dist", "build", "node_modules"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".yml", ".yaml", ".toml", ".ps1", ".cmd", ".bat", ".spec",
                 ".cfg", ".ini", ".env", ".html", ".css", ".lock", ".sh", ".gitignore", ".example", ""}
RISKY_FILES = re.compile(r"(^|/)(\.env|.*\.log|debug_.*|.*_ocr\.png|account_state\.json|app_settings\.json|"
                         r"support_reminder\.json|tutorial_done\.json|mastery_list\.json|arcane_stock\.json|"
                         r"sets_cache\.json|update_check\.json)$", re.I)

PATTERNS = {
    "e-mail address": re.compile(r"\b[\w.+-]+@(?!users\.noreply\.github\.com)[\w-]+\.[\w.-]+\b"),
    "Windows user path": re.compile(r"[A-Za-z]:\\+Users\\+(?!<|%|\{|Public|Default|USERNAME|you|name)[^\\\s\"']+", re.I),
    "JWT / token": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "API key assignment": re.compile(r"(?i)\b(api[_-]?key|secret|password|token)\s*=\s*['\"][^'\"\s]{12,}['\"]"),
}

# Files that legitimately contain fake secrets for tests, or upstream attribution.
ALLOW = [
    re.compile(r"tests[\\/]"),
    re.compile(r"^tools[\\/]audit_personal_data\.py$"),
    re.compile(r"^release_materials[\\/]"),   # third-party license texts by their own authors
]
# E-mail-like text that is not personal (package metadata, placeholders).
EMAIL_OK = re.compile(r"@(example\.|localhost|users\.noreply|github\.com|pypi\.org|python\.org)", re.I)


def iter_files():
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        yield path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--extra", nargs="*", default=[], help="extra case-insensitive terms to flag")
    args = parser.parse_args()
    terms = {getpass.getuser().lower(), *[t.lower() for t in args.extra if t]}
    terms.discard("")
    problems: list[str] = []

    for path in iter_files():
        rel = path.relative_to(ROOT).as_posix()
        if RISKY_FILES.search(rel):
            problems.append(f"{rel}: file type that usually holds personal data (delete or git-ignore it)")
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES or path.stat().st_size > 2_000_000:
            continue
        if any(rule.search(rel.replace("/", "\\")) or rule.search(rel) for rule in ALLOW):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for label, pattern in PATTERNS.items():
                match = pattern.search(line)
                if match and not (label == "e-mail address" and EMAIL_OK.search(match.group(0))):
                    problems.append(f"{rel}:{number}: {label}: {match.group(0)[:60]}")
            lowered = line.lower()
            for term in terms:
                if term in lowered:
                    problems.append(f"{rel}:{number}: contains '{term}'")

    for problem in problems:
        print(problem)
    print(f"\n{len(problems)} finding(s).")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
