---
name: local-tts-configure
description: Install, diagnose, and configure the local `tts` CLI (local-tts) — backends (llama.cpp, piper, OpenAI-compatible), voices for a given language, playback, and the per-language provider memory. Use when text-to-speech is missing or broken, when the user wants a different or better voice, when they need a new language, or when they ask to change any speech setting.
---

# Configuring `local-tts`

Use this when speech does not work yet, sounds wrong, or needs a new language. For simply
speaking text that already works, use the `local-tts-speak` skill.

## Always start here

```bash
tts check
```

It prints the config file path, the default backend, one line per backend with `[ok]` or
`[--]` plus the reason, and the detected audio players. **Read it before changing
anything** — it usually names the exact problem.

`[--]` on a backend the user does not use is fine. Only the backend they need must be
`[ok]`.

## Rules

- **Ask before installing anything, downloading a voice (~60 MB each), or running `sudo`.**
- Show `tts check` output to the user rather than paraphrasing it.
- Never edit the config JSON by hand; use `tts config --set` so validation applies.
- If `tts` itself is missing, that is a full install: follow `AGENT_INSTALL.md` in the
  local-tts repository, not this skill.

## The four backends

| Backend | Offline | Languages | Needs |
| --- | --- | --- | --- |
| `llamacpp` (default) | yes | **English, Chinese, Japanese, Korean only** | `llama-tts` binary |
| `piper` | yes | ~40 languages, fast | `piper` binary + a `.onnx` voice |
| `openai` | no | whatever the endpoint offers | a URL, and a key only for api.openai.com |
| `command` | yes | whatever the tool offers | any binary that writes a WAV |

**The single most common problem:** the user's text is not in one of llamacpp's four
languages, so it is pronounced with English phonetics. The fix is piper, not a setting.

## Adding a language with piper

Check whether piper exists first:

```bash
tts check | grep piper
```

If it is missing, install it **in its own virtualenv** — piper pulls in onnxruntime and
numpy (~200 MB), and local-tts is deliberately dependency-free.

**Linux / macOS:**

```bash
python3 -m venv ~/.local/share/piper-venv
~/.local/share/piper-venv/bin/pip install piper-tts
PIPER=~/.local/share/piper-venv/bin/piper
VOICES=~/.local/share/piper-voices
```

**Windows (PowerShell):**

```powershell
py -m venv $env:LOCALAPPDATA\piper-venv
& "$env:LOCALAPPDATA\piper-venv\Scripts\pip.exe" install piper-tts
$PIPER = "$env:LOCALAPPDATA\piper-venv\Scripts\piper.exe"
$VOICES = "$env:LOCALAPPDATA\piper-voices"
```

Note the differences on Windows: the launcher is `py` (or `python`, never `python3`), the
scripts live in `Scripts\` rather than `bin/`, and executables end in `.exe`.

Then pick a voice. **Ask the user which language and region** — accent matters to people:

```bash
# Linux / macOS — list every voice, then fetch one
~/.local/share/piper-venv/bin/python -m piper.download_voices
mkdir -p ~/.local/share/piper-voices && cd ~/.local/share/piper-voices
~/.local/share/piper-venv/bin/python -m piper.download_voices es_MX-claude-high
```

```powershell
# Windows
& "$env:LOCALAPPDATA\piper-venv\Scripts\python.exe" -m piper.download_voices
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\piper-voices" | Out-Null
Set-Location "$env:LOCALAPPDATA\piper-voices"
& "$env:LOCALAPPDATA\piper-venv\Scripts\python.exe" -m piper.download_voices es_MX-claude-high
```

Voice names read `<lang>_<REGION>-<speaker>-<quality>`, quality being
`x_low | low | medium | high`. Prefer the region matching the user's audience — `es_MX` for
Latin America, `es_ES` for Spain, `pt_BR` vs `pt_PT`, and so on. Voices come from
`rhasspy/piper-voices` on Hugging Face: public, MIT/CC, **no API token or account**.

Register it and record the language preference:

```bash
# Linux / macOS
tts config --set piper.binary=~/.local/share/piper-venv/bin/piper
tts config --set piper.model=~/.local/share/piper-voices/es_MX-claude-high.onnx
tts languages --set es=piper:~/.local/share/piper-voices/es_MX-claude-high.onnx
tts --lang es "Prueba de voz."      # verify it out loud
```

```powershell
# Windows — pass full paths; ~ is not expanded by PowerShell
tts config --set "piper.binary=$env:LOCALAPPDATA\piper-venv\Scripts\piper.exe"
tts config --set "piper.model=$env:LOCALAPPDATA\piper-voices\es_MX-claude-high.onnx"
tts languages --set "es=piper:$env:LOCALAPPDATA\piper-voices\es_MX-claude-high.onnx"
tts --lang es "Prueba de voz."
```

Recording the language is not optional bookkeeping — it is how every agent knows what to
use next time.

## The language memory

```bash
tts languages                                   # what is remembered
tts languages --set es=piper:/path/voice.onnx    # record
tts languages --set en=llamacpp                  # provider only
tts languages --forget de                        # drop one
```

Lookups match the specific tag before the base one, so `es-MX` wins over `es` when both
exist. Update this whenever the user expresses a preference, and confirm what you recorded.

## Playback control

Audio started with `tts -b` is detached, so it can be controlled afterwards:

```bash
tts playback     # playing / paused / nothing, plus the file
tts stop
tts pause        # POSIX only (Linux, macOS, WSL)
tts resume
```

If the user reports that audio "will not stop", run `tts playback` first — if it reports
nothing, the sound is coming from something else, not this CLI. A stale state file is
harmless: the next `stop` clears it.

## Status-bar hook (progress in the host's own UI, not chat)

`local-tts-speak` prints a status line for each thing it plays. If the user finds that
noisy and asks for progress somewhere else — "can this show in my status bar instead",
"don't spam the chat" — a real hook exists for two hosts, verified against their own
settings schemas, not assumed:

| Host | Mechanism |
| --- | --- |
| Claude Code | `~/.claude/settings.json` → `statusLine.command`, with a real `refreshInterval` timer |
| Qwen Code | `~/.qwen/settings.json` → `ui.statusLine.command`, same idea |

```bash
tts hooks                    # what's detected, installed, and why the rest can't do this
tts hooks --install          # install into every detected supported host
tts hooks --uninstall        # remove it, restoring whatever status line was there before
```

Everything else — Gemini CLI, Codex CLI, OpenCode, Cursor, Windsurf, GitHub Copilot CLI —
reports a specific reason it isn't supported (`tts hooks` prints it): no such mechanism
exists yet for most of them (open upstream feature requests, not a gap in this tool), and
Cursor/Windsurf would need a full IDE extension rather than a lightweight hook. Don't
promise this to a user on one of those hosts; tell them why it isn't available there yet.

**Install never rewrites `statusLine.command` — it appends into the file it already points
to.** This is load-bearing, not a nicety: an earlier version replaced the pointer with its
own wrapper and saved the old command as a string to restore later, and that broke a real
Boost installation — when Boost's own reinstall regenerated its script, our saved reference
went stale, and because we now owned the slot, Boost's own installer no longer recognized
it as its own and silently stopped rendering. The fix is structural: if a status line is
already configured, we add a small marked block to the *end of that script file* instead,
so whatever tool owns the slot keeps owning it and keeps running exactly as it did. Idle,
output is byte-for-byte what it was before installing. Reinstalling replaces our block in
place, never duplicating it; `--uninstall` removes only our block, leaving everything else
untouched.

This only works when the existing command is a **plain path to a writable script file** —
not a one-liner, not something with arguments, not something unwritable. If it doesn't
qualify, install refuses and prints the exact block to add by hand, or the user can choose
`--force` to fall back to the old replace-and-chain behavior — tell them plainly that this
means the tool that used to own the slot stops running, which is a real cost, not a free
upgrade; don't reach for `--force` as a default when append fails, ask first.

After an append-mode install, no restart is needed — it takes effect on the very next
status-bar refresh, since only the script's own content changed. A fresh install with
nothing configured before (or `--force`) does change `statusLine.command`/`refreshInterval`
directly, and *does* need a restart. Verify with `tts hooks --status`, which prints
`active`/`inactive` — that only flips to `active` once the host has actually called the
hook at least once, so give it a moment right after.

**If the user says the bar "looks frozen"**, that's the default: appending into an existing
status line leaves the refresh cadence exactly as it was, and without a timer configured
that means only redrawing on host events (a new message, a permission-mode change), not a
per-second tick. Offer `--refresh-interval N` — ask what cadence they want rather than
picking one:

```bash
tts hooks --install claude-code --refresh-interval 2   # real timer, ticks live
tts hooks --install claude-code --refresh-interval 0   # explicitly event-based (removes any existing timer)
```

Tell them plainly that this also changes how often the *other* tool's script re-runs, not
just ours — that's real overhead if the existing tool does anything non-trivial per call,
which you have no visibility into. `0` is a deliberate choice (explicitly no timer), not
the same as omitting the flag (leave whatever cadence was already configured). This does
write to settings.json — only the `refreshInterval` key, `command` is still never touched —
so it needs a restart.

**Multiple sessions on the same machine are correctly isolated for Claude Code**, verified
against a live capture. Playback started with `--session` is looked up by that same id when
rendering — for a fresh/`--force` install, from the `session_id` field in the host's JSON
payload; for the (default) appended mode, from `$CLAUDE_CODE_SESSION_ID` in the
environment, since stdin may already be consumed by the script we appended into. Only
`CLAUDE_CODE_SESSION_ID` is verified so far — Qwen Code's shell tool sets no session-id env
var (checked its docs), so session isolation there is unconfirmed; a single Qwen session
still works correctly, it's only concurrent Qwen sessions that aren't proven yet. If a
user reports their status bar showing playback they didn't start, or not showing playback
they did, check `tts hooks --status` and confirm they're on a build where `-b` actually
uses `--session` — that correlation is what makes this work, not anything host-specific.

## Other settings

```bash
tts config --show                       # effective configuration
tts config --path                       # where it lives
tts config --set provider=piper         # default backend
tts config --set play=false             # never auto-play
tts config --set player=ffplay          # force a playback command

# llama.cpp performance
tts config --set llamacpp.threads=8
tts config --set llamacpp.gpu_layers=99
tts config --set llamacpp.max_words=26  # prompt chunk size; 0 disables chunking

# an OpenAI-compatible server (no key needed for a local one)
tts config --set openai.base_url=http://localhost:8880/v1
tts -p openai -o out.mp3 "hello"
```

Per-run overrides that change nothing permanently: `tts -s threads=4 "..."`.

## Diagnosing

| `tts check` / error says | Meaning | Fix |
| --- | --- | --- |
| `'llama-tts' not found on PATH` | llama.cpp missing | install it, or `tts config --set llamacpp.binary=/full/path` |
| `model set without a vocoder` | llamacpp needs both files | set `llamacpp.vocoder`, or clear `llamacpp.model` to use defaults |
| `no voice model configured` | piper has no `.onnx` | download one, set `piper.model` |
| `('x' is not on PATH)` on `command` | template points at a missing binary | install it or change the template |
| `no api_key and $OPENAI_API_KEY is unset` | only matters if they use `openai` | export the key, or ignore |
| `players : none found` | no audio player (Linux only) | install `ffmpeg`, or always use `-o` |
| right words, wrong accent | llamacpp used for an unsupported language | switch that language to piper |

Useful when something is off: `--verbose` streams the backend's own stderr, and `--dry-run`
prints the exact command (plus the chunk plan) without running it.

## Platform differences that actually matter

| | Linux | macOS | Windows |
| --- | --- | --- | --- |
| Python launcher | `python3` | `python3` | `py` or `python` |
| venv binaries | `<venv>/bin/` | `<venv>/bin/` | `<venv>\Scripts\` + `.exe` |
| Config file | `~/.config/local-tts/config.json` | same | `%APPDATA%\local-tts\config.json` if `XDG_CONFIG_HOME` is unset, else that |
| Audio player | needs one installed | `afplay`, built in | PowerShell player, built in |
| llama.cpp | package manager, release zip, or build | `brew install llama.cpp` | `winget install llama.cpp` |
| `~` in a config value | expanded by the CLI | expanded | **use a full path**; PowerShell will not expand it |

The CLI itself expands `~` in config values on every platform, so
`tts config --set piper.model=~/voices/x.onnx` is fine — the risk on Windows is the *shell*
not expanding it before the CLI sees it, which is why the examples above use
`$env:LOCALAPPDATA`.

Run commands one at a time rather than `&&`-chaining them; `&&` is not valid in older
PowerShell.

## Finish

Re-run `tts check`, speak one real sentence in the user's language, and tell them what you
changed, what you recorded in `tts languages`, and the one command they will use daily.
