# Publishing Parallel to GitHub

A checklist for the first upload. Do the steps in order and stop if any check fails.

## 1. Choose the public identity first

- Create the GitHub account (or organisation) under the public handle you want on this
  project, for example the separate handle you use for hobby and game tools.
- In GitHub, open Settings, Emails, and turn on **Keep my email addresses private** and
  **Block command line pushes that expose my email**.
- Note your no-reply address (`<id>+<handle>@users.noreply.github.com`).
- The licence holder in `LICENSE` is a public name. Change it if you choose another handle.

## 2. Set Git to that identity (in this project only)

```powershell
git init
git config user.name "YOUR-PUBLIC-HANDLE"
git config user.email "YOUR-NOREPLY-ADDRESS@users.noreply.github.com"
git config core.hooksPath .githooks
```

## 3. Check for personal data

```powershell
python -B tools/audit_personal_data.py --extra "your real name" "your personal e-mail" "your PC user name"
```

It must print `0 finding(s)`, apart from log files that are git-ignored.

## 4. Refresh the reviewed file list and verify

```powershell
python -B tools/update_publication_manifest.py          # shows what changed
python -B tools/update_publication_manifest.py --apply  # writes PUBLIC_SOURCE_FILES.json and .gitignore
python -B tools/verify_public_source.py
```

The project only publishes files named in `PUBLIC_SOURCE_FILES.json`. Everything else
(your settings, token, logs, screenshots) stays out by design.

## 5. Commit and push

```powershell
git add .
git commit -m "Initial commit"
python -B tools/verify_public_source.py --require-git --history
git remote add origin https://github.com/YOUR-HANDLE/parallel.git
git push -u origin main
```

Never use `git add -f`. Review the staged list before pushing. Run `gitleaks` too if you
have it (see `SOURCE_PUBLICATION.md`).

## What is intentionally not in the repository

Your market token (`.env`), `app_settings.json`, account and inventory data, mastery list,
arcane stock, caches, logs, screenshots, build output and virtual environments all live
outside the project folder or are git-ignored.

## Credit

Parallel is based on Sidro by Hitakumori (MIT). Keep `LICENSE` and `NOTICE.md` intact.
