"""Rebuild the reviewed public-source list (PUBLIC_SOURCE_FILES.json) and .gitignore for this fork.

The upstream guard only lets files named in PUBLIC_SOURCE_FILES.json (with their SHA-256
hashes) be committed. After editing the project, run this to review the current files and
refresh the list. Every included file goes through the same privacy scan as
tools/verify_public_source.py (e-mails, tokens, personal Windows paths).

    python -B tools/update_publication_manifest.py          # dry run, shows what would change
    python -B tools/update_publication_manifest.py --apply  # rewrite manifest and .gitignore

New files are included automatically when they sit in the project root, tests/ or tools/ and
have a source/doc suffix. Anything else needs adding by hand (and a review).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_public_source as guard  # noqa: E402

ROOT = guard.ROOT
AUTO_DIRS = ("", "tests", "tools")
AUTO_SUFFIXES = {".py", ".md", ".ps1", ".bat", ".txt", ".toml", ".spec", ".ico", ".png", ".lock", ".json"}
# Internal reports, scratch notes, captures and logs never go public.
EXCLUDE = re.compile(
    r"(^|/)(REPORT_[^/]*|[^/]*_notes\.md|build_[^/]*\.md|debug_[^/]*|test_capture[^/]*|[^/]*\.log|"
    r"account_state\.json|app_settings\.json|tutorial_done\.json|mastery_list\.json|arcane_stock\.json|"
    r"sets_cache\.json|arcanes_cache\.json|update_check\.json)$"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect(previous: dict[str, str]) -> dict[str, str]:
    names = {name for name in previous if (ROOT / name).is_file()}
    for directory in AUTO_DIRS:
        folder = ROOT / directory
        for path in sorted(folder.iterdir()):
            if not path.is_file() or path.name.startswith(".") or path.suffix.lower() not in AUTO_SUFFIXES:
                continue
            rel = path.relative_to(ROOT).as_posix()
            if rel == guard.MANIFEST or EXCLUDE.search(rel):
                continue
            names.add(rel)
    return {name: "" for name in sorted(names)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the manifest and .gitignore")
    args = parser.parse_args()

    try:
        previous = guard.load_manifest(ROOT)
    except Exception:
        # Tolerate a damaged or BOM-prefixed manifest: keep the paths it lists.
        try:
            raw = json.loads((ROOT / guard.MANIFEST).read_text(encoding="utf-8-sig"))
            previous = dict(raw.get("files", {}))
        except (OSError, ValueError):
            previous = {}
    files = collect(previous)

    problems = []
    for name in list(files):
        try:
            guard.validate_path(name)
            guard.check_content(name, (ROOT / name).read_bytes())
        except guard.PublicationError as error:
            problems.append(f"{name}: {error}")
            del files[name]
    for required in (".gitignore", "tools/verify_public_source.py", "SOURCE_PUBLICATION.md", "LICENSE"):
        files.setdefault(required, "")

    # .gitignore is generated from the file list, so write it before hashing it.
    gitignore = guard.render_gitignore(files)
    removed = sorted(set(previous) - set(files))
    added = sorted(set(files) - set(previous))
    print(f"files: {len(files)}  added: {len(added)}  removed: {len(removed)}")
    for label, group in (("added", added), ("removed", removed)):
        for name in group:
            print(f"  {label}: {name}")
    for problem in problems:
        print(f"  BLOCKED: {problem}")
    if problems:
        print("\nFix or remove the blocked files before publishing.")
        return 1
    if not args.apply:
        print("\nDry run only. Re-run with --apply to rewrite the manifest and .gitignore.")
        return 0

    (ROOT / ".gitignore").write_bytes(gitignore.encode("utf-8"))
    hashed = {name: sha256(ROOT / name) for name in sorted(files)}
    manifest = json.dumps({"files": hashed, "schema_version": 1}, indent=2) + "\n"
    (ROOT / guard.MANIFEST).write_bytes(manifest.encode("utf-8"))
    guard.verify_worktree(ROOT, guard.load_manifest(ROOT))
    print(f"\nWrote {guard.MANIFEST} and .gitignore; worktree verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
