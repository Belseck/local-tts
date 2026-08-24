---
name: local-tts-update
description: Update an already-installed local-tts CLI to the latest version — locate the repo behind the running `tts` command, pull, reinstall only if needed, and refresh the skill/hook files that are copies rather than live links to it. Use when the user asks to update, upgrade, or get the latest local-tts, or when something documented (a flag, a setting, a skill) doesn't match what `tts` actually does.
---

# Updating `local-tts`

local-tts has no auto-update and isn't published on PyPI — it lives in a git clone the
user installed with `pip install -e .` (see `AGENT_INSTALL.md` in the repo). Updating means
pulling that repo, then refreshing the few things that are **snapshots**, not symlinks, of
its content.

## 1. Find the repo behind the installed `tts`

Don't ask where it was cloned — resolve it from the running binary, the same way you would
diagnose any other "which install is this" question:

```bash
TTS_BIN=$(command -v tts || command -v local-tts)
VENV_PY=$(head -1 "$TTS_BIN" | sed 's/^#!//')     # the shebang is an absolute interpreter path
"$VENV_PY" -m pip show local-tts
```

- **`Editable project location: /path/to/repo`** present → this is the normal install (a
  venv plus `pip install -e .`, optionally symlinked onto `PATH`). That path is the repo —
  `cd` into it for every step below.
- **No "Editable project location"** → installed with `pipx install .` (the alternative the
  README documents). pipx built a static copy; pulling a repo won't touch it. You need the
  *source* repo the user cloned to run `pipx install . --force` from — ask them for its
  path if you don't already know it. There's no way to recover it from a pipx install alone.

On Windows the shebang line isn't plain text the same way; instead run
`python -m pip show local-tts` using whichever `python` the user activated when they
installed it (ask if that's unclear).

## 2. Check for local changes before pulling

```bash
cd <repo>
git status --short
```

If this prints anything, **stop and ask** before pulling — do not discard someone's
in-progress edits with a `git pull` that could conflict or a `reset`. Uncommitted changes
under `src/` are the most likely reason someone is running an editable install in the first
place: they're customizing it.

## 3. See what's new, then pull

```bash
git fetch origin
git log HEAD..origin/main --oneline   # substitute the actual default branch if it isn't main
git pull
```

Keep that commit list — you'll want it to tell the user what actually changed, not just
"update complete."

## 4. Reinstall — only sometimes needed, but always safe

**Editable install:** since `src/localtts/` is loaded live, ordinary code changes take
effect on the very next `tts` invocation — no reinstall required. The one case that *does*
need one is a `pyproject.toml` change (a new entry point, a version bump, a bumped Python
floor) or a genuinely new dependency. Rather than diffing `pyproject.toml` yourself to
decide, just run it — it's idempotent and costs nothing here, since the package has zero
runtime dependencies:

```bash
pip install -e .        # using the venv's own pip (activate it, or call it by full path)
```

**pipx install:** pipx never re-reads the source directory on its own — always reinstall:

```bash
pipx install . --force
```

## 5. Refresh what lives *outside* the repo

Two things `local-tts` writes elsewhere are **copies made at install time**, not live
pointers into the repo — pulling does not update them on its own:

- **Agent skills.** `tts skills --install` writes `agent_skills/*.md` into each detected
  agent's own skill directory (e.g. `~/.claude/skills/local-tts-speak/SKILL.md`). If a
  skill's instructions changed upstream — this one included — every agent is still reading
  its old copy until it's reinstalled:

  ```bash
  tts skills --install
  ```

  Safe to run unconditionally: it's the same idempotent write `tts skills` already
  documents, and it only touches agents actually detected on the machine.

- **The status-bar hook**, if one is installed (`tts hooks --status` to check first). The
  wrapper script `tts hooks --install` writes is also generated once, not linked live:

  ```bash
  tts hooks --install     # only if `tts hooks --status` shows one is active
  ```

## 6. Verify, and report what changed

```bash
tts --version
tts check
tts skills
```

Confirm the version actually moved and that `tts check` still shows `[ok]` on the user's
default provider — a missed `pyproject.toml`-driven reinstall (step 4) is exactly the kind
of thing this would catch. Then tell the user what changed, using the commit list from step
3, rather than just reporting success silently.
