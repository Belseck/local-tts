---
name: local-tts-update
description: Update an already-installed local-tts CLI to the latest version — locate the repo behind the running `tts` command, pull, reinstall only if needed, refresh the skill/hook files that are copies rather than live links to it, and check whether a `command` provider template can now migrate to a real native provider (e.g. kokoro, rvc). Use when the user asks to update, upgrade, or get the latest local-tts, or when something documented (a flag, a setting, a skill) doesn't match what `tts` actually does.
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

## 4. Refresh yourself, and pick up the current instructions if you just changed

**The instructions you are following right now are a snapshot** — installed into your
skills directory the last time someone ran `tts skills --install`, not read live from the
repo you just pulled. The update process itself can change between versions (a step added,
removed, reordered, a flag renamed), so don't blindly keep following what's already loaded
once a newer copy might exist a few directories away.

If step 3's commit list was **empty** (nothing to pull), skip this step — there is nothing
newer to pick up. Otherwise, refresh every installed skill from what you just pulled — the
same idempotent write `tts skills` always documents, so it costs nothing even when this
skill didn't change:

```bash
tts skills --install
```

Then print this skill's **current** content directly, straight from the install you just
refreshed rather than whatever's cached in your own context or your host's skill loader:

```bash
tts skills --print local-tts-update
```

**Treat that printed text as your instructions for the rest of this task, replacing what
you started with.** This is the deterministic path — it does not depend on whether your
host re-reads a skill file mid-session (most don't reliably), because the command output
lands directly in what you can already see, the same as any other command's output. Note
where you are (you've done steps 1-4) and resume from step 5 in whatever the printed
version calls it — steps may have moved.

Keep what you already learned in steps 1-3 (the repo path, the commit list) — you're
continuing the same task, not starting over.

At the very end (the last step, wherever it ends up), **tell the user to restart their
agent or start a new session.** Printing solved *this* conversation; it did not change what
your host loads automatically next time, and if step 4's instructions themselves moved or
changed, the only way everything (not just this one update) is guaranteed current going
forward is a fresh start.

## 5. Reinstall — only sometimes needed, but always safe

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

## 6. Refresh what lives *outside* the repo

Agent skills were already refreshed in step 4. One more thing `local-tts` writes elsewhere
is a **copy made at install time**, not a live pointer into the repo — pulling does not
update it on its own:

**The status-bar hook**, if one is installed (`tts hooks --status` to check first). The
wrapper script `tts hooks --install` writes is also generated once, not linked live:

```bash
tts hooks --install     # only if `tts hooks --status` shows one is active
```

**A kokoro or rvc persistent server**, if either is configured (`kokoro.server_url` /
`rvc.server_url` set — `tts check` shows whether one is currently running). Its script is
also a copy, written into the provider's own venv, never touched by `git pull`. This only
matters if step 3's commit list mentions the server protocol (the `/synthesize` or
`/convert` request shape) changing — if so, kill the running process; the next call starts
a fresh one from whatever script is on disk. Otherwise leave it running, there's nothing to
do here.

## 7. Check for a `command` provider that a new update now supports natively

Before local-tts had a real `kokoro` or `rvc` provider, the only way to drive either was
the generic `command` escape hatch — a hand-written template like
`kokoro-tts -o {output} -v ef_dora -l es {text}`. If an update just added native support
for something the user was already running that way, they're now on the harder-to-maintain
path for no reason. Check every time you update, not just once:

```bash
tts config --detect-migrations
```

Prints nothing if there's nothing to migrate — nothing to do. If it finds a match, it
prints the exact `tts config --set` commands that would switch to the native provider
(never applies them itself). **Ask before running them** — the same rule as everywhere
else this skill touches configuration. If the user says yes:

```bash
tts config --set kokoro.voice=ef_dora    # whatever --detect-migrations printed
tts config --set kokoro.lang=es
tts check                                 # confirm the native provider is [ok] too
```

Leave `command.template` itself alone — migrating only adds the native provider's
settings, it never deletes the old template, so it stays there as a fallback. If the
migrated provider was the user's actual default (`--detect-migrations` says so
explicitly, appending a `provider=<name>` line), ask separately before switching that,
since it changes what plays by default rather than just adding an option.

This detector only knows about tools with a real provider today — it will not (yet) flag
some other CLI wired through `command`, and that's fine, there's nothing to migrate it to.

## 8. Verify, and report what changed

```bash
tts --version
tts check
tts skills
```

Confirm the version actually moved and that `tts check` still shows `[ok]` on the user's
default provider — a missed `pyproject.toml`-driven reinstall (step 5) is exactly the kind
of thing this would catch. Then tell the user what changed, using the commit list from step
3, rather than just reporting success silently. Mention anything migrated in step 7 too.

**If step 4 printed this skill's content instead of re-invoking it**, close by telling the
user to restart their agent or start a new session — say so plainly, it's not optional
cleanup. This update is already complete either way; the restart is about their *next*
conversation loading everything (not just this skill) the normal way instead of whatever
got it here this time.
