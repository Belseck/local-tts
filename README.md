# local-tts

<p align="center">
  <img src="assets/logo.jpg" alt="local-tts logo" width="160">
</p>

<p align="center">
  <a href="https://www.producthunt.com/products/localtts?embed=true&amp;utm_source=badge-featured&amp;utm_medium=badge&amp;utm_campaign=badge-local-tts" target="_blank" rel="noopener noreferrer"><img alt="local-tts - Make your coding agent talk to you! Offline! | Product Hunt" width="250" height="54" src="https://api.producthunt.com/widgets/embed-image/v1/featured.svg?post_id=1230101&amp;theme=neutral&amp;t=1787592484631"></a>
  <br>
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-blue.svg">
  <img alt="Python 3.9+" src="https://img.shields.io/badge/python-3.9%2B-blue.svg">
  <br>
  <a href="https://buymeacoffee.com/rperez93" target="_blank" rel="noopener noreferrer"><img alt="Buy Me A Coffee" src="https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support-ffdd00?logo=buymeacoffee&logoColor=black"></a>
</p>

Make your coding agent talk to you!

A tiny command-line text-to-speech tool. It shells out to **Kokoro-82M** by default —
~40 languages from one small model — so speech is generated locally and offline.

```console
$ tts "Hello from my terminal."
$ echo "Read this out loud." | tts
$ tts -f chapter1.txt -o chapter1.wav
```

**Zero runtime dependencies.** The package installs nothing but itself — no
`requests`, no `numpy`, no audio libraries. Everything is Python's standard library
plus binaries you already have (or install once, on your terms).

---

## Make Claude talk like Jarvis. Or Cortana. In Spanish.

Your coding agent already writes your code. With an RVC voice model it can *sound* like
whoever you want while it does — offline, on your own machine, no API key.

```console
$ tts --lang en "Good evening. The tests are passing, sir."
$ tts --lang es "Buenas noches. Todas las pruebas pasaron."
```

One `rvc` server holds both voices resident and picks one per language:

```bash
tts config --set rvc.base_provider=kokoro          # Kokoro speaks; RVC changes who
tts config --set kokoro.language_voices.en=bm_george
tts config --set kokoro.language_voices.es=ef_dora
tts config --set rvc.language_models.en=jarvis     # your own trained .pth
tts config --set rvc.language_models.es=cortana-es
tts languages --set en=rvc
tts languages --set es=rvc
```

Now every agent on the machine that knows about `tts` speaks in that voice, in the right
language, because the preference lives in one config file rather than in one chat.

**It acts, too.** Tone tags change the delivery per sentence:

```console
$ tts "<calm>Deploy finished.</calm> <urgent>But staging is down.</urgent>"
```

**And it answers fast.** Fragments start playing as soon as the first one is
synthesized, so a long passage begins in about a second instead of after the whole
thing renders — measured on a 19-span story, 9.6s to first sound instead of 40.5s.

> Bring your own `.pth` — local-tts wires up a voice model you already have or trained;
> it does not distribute voices, and cloning a real person's voice without their consent
> is not what this is for.

See [Providers → `rvc`](#rvc--voice-conversion-not-installed-automatically) for the full setup.

---

## Contents

- [Make Claude talk like Jarvis. Or Cortana. In Spanish.](#make-claude-talk-like-jarvis-or-cortana-in-spanish)
- [Requirements](#requirements)
- [Install](#install)
  - [Install with an AI agent](#install-with-an-ai-agent)
- [Updating](#updating)
- [Quick start](#quick-start)
- [Usage](#usage)
- [Background playback](#background-playback)
- [Coding-agent skills](#coding-agent-skills)
- [Status-bar hook](#status-bar-hook)
- [Language memory](#language-memory)
- [Providers](#providers)
- [Configuration](#configuration)
- [Audio playback](#audio-playback)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

---

## Requirements

| What | Why | Required? |
| --- | --- | --- |
| Python ≥ 3.9 | runs the CLI | yes |
| Linux, macOS or Windows | all three supported | — |
| `llama-tts` from llama.cpp | the default speech backend | yes, for the default provider |
| An audio player (`ffplay`, `paplay`, `aplay`, …) | playing the result | only if you want playback |

### Installing llama.cpp

`local-tts` calls the `llama-tts` binary; it does **not** bundle or build llama.cpp.

```bash
# macOS / Linux (Homebrew)
brew install llama.cpp

# Windows
winget install llama.cpp

# Prebuilt binaries for every platform
# https://github.com/ggml-org/llama.cpp/releases  (grab llama-<build>-bin-<platform>.zip)

# Or build from source
git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp
cmake -B build
cmake --build build --config Release -j
# binaries land in build/bin — put that on your PATH, or see "Configuration" below
```

Verify it is reachable:

```bash
llama-tts --version
```

If `llama-tts` is not on your `PATH`, point `local-tts` at it directly:

```bash
tts config --set llamacpp.binary=/path/to/llama.cpp/build/bin/llama-tts
```

### Speech models

You do not need to download anything by hand. On the first run, `llama-tts`
fetches its default [OuteTTS](https://huggingface.co/OuteAI) weights plus the
WavTokenizer vocoder into the Hugging Face cache (`~/.cache/huggingface/hub`,
about **640 MB** total). Later runs use the cache and work fully offline.

To use your own GGUF weights instead, see [Using your own models](#using-your-own-models).

---

## Install

Everything happens inside a virtual environment so nothing touches your system Python.

```bash
git clone <this-repo> local-tts
cd local-tts

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

python -m pip install --upgrade pip   # needs pip >= 24.2 for the package metadata
pip install -e .
```

That puts two equivalent commands on your `PATH` (while the venv is active): `tts`
and `local-tts`.

<details>
<summary>Prefer a system-wide command without activating the venv?</summary>

```bash
# pipx keeps the tool isolated but always on your PATH
pipx install .

# or symlink the venv entry point somewhere on your PATH
ln -s "$PWD/.venv/bin/tts" ~/.local/bin/tts
```

The symlink works from any directory because the entry point's shebang is an absolute path
to the venv's interpreter. The install is editable, so edits to `src/localtts/` take effect
immediately — but do not move or delete the repo, since the link points into it. Undo with
`rm ~/.local/bin/tts`.
</details>

### Install with an AI agent

If you use an AI coding agent (Claude Code, Cursor, Copilot, …), the whole setup —
including detecting what is already on the machine, installing the backends, and creating
the global symlink — is scripted for it in **[`AGENT_INSTALL.md`](AGENT_INSTALL.md)**.

Point your agent at this repository and say what you want:

> **"Install this with the link."**

or, more specifically:

> "Read AGENT_INSTALL.md and install local-tts with piper in Spanish, and make it global."

The agent will detect what you already have, ask once about anything missing, and validate
the result with `tts check` plus a real synthesis. It is written to **ask before installing
anything, downloading a model, creating a symlink, or running `sudo`** — so you approve
each decision rather than discovering it afterwards.

Phrases it understands without further questions:

| Say | Meaning |
| --- | --- |
| "with the link" / "make it global" | create the `~/.local/bin/tts` symlink |
| "with piper" / "for Spanish" (any language) | also install piper and a matching voice |
| "just the CLI" | package only, no backends, no symlink |
| "install everything" | all steps approved, plan still shown first |

Prefer doing it yourself? Everything the agent does is the same set of commands documented
in [Requirements](#requirements), [Install](#install) and [Providers](#providers) below.

### Verify the install

```bash
tts check
```

```
config file : /home/you/.config/local-tts/config.json (not created yet)
default     : kokoro

[ok] llamacpp  /usr/local/bin/llama-tts -> default OuteTTS (downloaded on first run)
[--] openai    https://api.openai.com/v1 (no api_key and $OPENAI_API_KEY is unset)
[--] piper     piper: 'piper' not found on PATH. ...
[ok] command   espeak-ng -w {output} {text}

players     : ffplay, paplay
```

Only the line matching your default provider has to say `[ok]`.

---

## Updating

There's no auto-update and no PyPI package — `local-tts` lives in the git clone from
[Install](#install). Updating means pulling that repo, plus refreshing the couple of things
that are copies of it rather than live links.

```bash
cd local-tts               # the repo you cloned in Install
git status --short         # make sure there's nothing uncommitted first
git pull

# editable install (the default): src/ changes are live immediately. Rerunning this
# is still worth it — it's a no-op most of the time, but it's what picks up a
# pyproject.toml change (entry point, version, python floor), and it costs nothing
# since the package has zero runtime dependencies
pip install -e .

# pipx install instead? pipx never re-reads the source directory on its own:
pipx install . --force
```

Two more things are **snapshots taken at install time**, not symlinks into the repo, so
pulling doesn't update them on its own:

```bash
tts skills --install    # refreshes the skill copies every detected agent is reading
tts hooks --install      # only if `tts hooks --status` shows one is active
```

Then `tts --version` and `tts check` to confirm it landed.

If you use a coding agent, the whole thing — including finding the repo behind whatever
install method you used — is the **`local-tts-update`** skill from
[Coding-agent skills](#coding-agent-skills); just say "update local-tts."

---

## Quick start

```bash
# speak an argument
tts "The quick brown fox jumps over the lazy dog."

# speak a pipe
git log -1 --format=%s | tts

# speak a file, save the audio instead of playing it
tts -f notes.md -o notes.wav

# save and play
tts -o greeting.wav --play "Good morning."

# narrate a markdown document: syntax stripped, long text chunked and joined
tts -f README.md -o readme.wav

# see the exact backend command (and the chunk plan) without running it
tts --dry-run -f README.md
```

The first invocation is slow: it downloads the model. After that a short sentence
takes a couple of seconds on CPU.

---

## Usage

```
tts [options] [TEXT ...]
tts providers
tts check
tts config [--show | --path | --init | --set KEY=VALUE]
```

| Option | Description |
| --- | --- |
| `TEXT ...` | Text to speak. Omit it to read stdin. |
| `-f, --file FILE` | Read the text from a file (`-` for stdin). |
| `--markdown` / `--no-markdown` | Force markdown stripping on or off (automatic for `.md` files). |
| `-o, --output FILE` | Write the audio here instead of playing it. |
| `-p, --provider NAME` | `kokoro` (default), `piper`, `rvc`, `llamacpp`, `openai`, `command`. |
| `-l, --lang CODE` | Use the backend and voice remembered for this language. |
| `-v, --voice VOICE` | Speaker file (llamacpp), `.onnx` voice (piper), or voice name (openai). |
| `-m, --model MODEL` | Override the provider's model for this run. |
| `-s, --set KEY=VALUE` | Override any provider setting for this run. Repeatable. |
| `-b, --background` | Play in the background, return immediately, keep the file and print its path. |
| `--play` | Play the audio *and* keep `--output`. |
| `--no-play` | Never play; just report the file path. |
| `--player CMD` | Force a playback command instead of autodetecting. |
| `--keep` | Keep the temporary file and print its path. |
| `--dry-run` | Print the backend command that would run, then exit. |
| `--verbose` | Show the backend's own (noisy) output. |
| `--version` | Print the version. |

Input precedence is `TEXT` → `--file` → stdin. Without `--output`, audio goes to a
temporary file that is played and then deleted (`--keep` keeps it).

**Markdown is handled for you.** Reading a `.md` file strips headings, emphasis, link
URLs, bullet markers, tables and fenced code blocks before synthesis, so none of it gets
read aloud. Override either way with `--markdown` / `--no-markdown`.

**Long documents are handled for you too.** Backends that need short prompts (`llamacpp`)
get the text split at sentence boundaries, synthesized piece by piece, and joined into a
single file with a short pause between pieces. Backends that manage long input themselves
(`piper`) receive it whole. See `max_words` below.

Exit codes: `0` success, `1` error (with a one-line message on stderr), `130` interrupted.

---

## Background playback

`--background` (`-b`) starts playback detached and returns straight away, so a long file
does not block the shell — or an agent driving it. The file is kept and its path printed.

```bash
$ tts -b --lang es -f documento.md
playing in the background (pid 4123, 0:12) — `tts stop` to end it, `tts playback` for progress
/tmp/local-tts-a1b2c3d4.wav
```

Control it afterwards, with elapsed time tracked against the file's real duration:

```bash
$ tts playback
playing [###########---------] 0:03 / 0:05 (pid 4123): /tmp/local-tts-a1b2c3d4.wav

tts pause
tts resume
tts stop
```

Starting a new background playback stops the previous one *in the same session*, so
voices never stack there. Separately, playback is also serialized machine-wide: if
another session (a second agent, a second terminal) already has audio playing, a new
`-b` call queues behind it instead of overlapping — only one file ever plays at a time,
no matter which provider or session started it. The CLI call itself still returns
immediately either way; only the actual audio start is deferred. `tts playback` shows
`0:00` and holds there while queued, then starts advancing once its turn begins.

`pause`/`resume` use `SIGSTOP`/`SIGCONT` and therefore work on Linux, macOS and WSL; on
native Windows they report that they are unsupported and `stop` is the control.

## Coding-agent skills

`local-tts` ships three skills that teach a coding agent to use it, and installs them into
whichever agents it finds on your machine:

- **`local-tts-speak`** — speak to the user. Triggers on "talk to me", "read this aloud",
  "narrate this file", "háblame", and so on. Instructs the agent to check the language
  memory first, to always use `-b` (and to run the command itself non-blocking), to play the
  whole thing regardless of length unless told otherwise, to offer `stop`/`pause`/`resume`,
  to always report the file path, and never to read secrets out loud.
- **`local-tts-configure`** — install, diagnose and configure: backends, voices for a new
  language, playback, and the per-language memory. Starts from `tts check` and asks before
  installing or downloading anything.
- **`local-tts-update`** — update an already-installed CLI to the latest version. Locates
  the repo behind the running `tts` command, pulls it, reinstalls only if that's actually
  needed, and refreshes the skill/hook files that are copies rather than live links to the
  repo — including its own file: since the update process can change between versions, it
  refreshes and re-invokes itself right after pulling, rather than finishing the rest of
  the update under instructions that might already be stale. See [Updating](#updating).

```bash
tts skills                       # what was detected, and what is installed
tts skills --install             # install into every detected agent
tts skills --install gemini      # or just one
tts skills --install --dry-run   # show the paths without writing
tts skills --uninstall           # remove them again
tts skills --print local-tts-update   # print one skill's current content to stdout
```

`--print` reads straight from this install, not the copy sitting in any agent's skill
directory — useful for a host that won't reliably pick up a changed skill file mid-session
(`local-tts-update` uses it on itself for exactly that reason, see [Updating](#updating)).

Restart the agent (or open a new session) afterwards so it picks them up.

| Agent | Installed as |
| --- | --- |
| Claude Code | `~/.claude/skills/<name>/SKILL.md` |
| Gemini CLI | `~/.gemini/skills/<name>/SKILL.md` |
| OpenCode | `<config>/opencode/skills/<name>/SKILL.md` |
| Qwen Code | `~/.qwen/skills/<name>/SKILL.md` |
| Codex CLI | section in `~/.codex/AGENTS.md` |
| Cursor | `~/.cursor/rules/local-tts.mdc` |
| Windsurf | `~/.codeium/windsurf/memories/local-tts.md` |
| GitHub Copilot | `<config>/github-copilot/local-tts-instructions.md` |

`<config>` is `%APPDATA%` on Windows and `~/.config` on Linux and macOS
(`$XDG_CONFIG_HOME` wins on any platform when set). Detection only writes where the agent's
directory already exists, so nothing is created for agents you do not use.

Agents with a real skill mechanism get one file per skill. Agents that read a single flat
instructions file get a block delimited by `<!-- BEGIN local-tts skills -->` markers —
**anything already in that file is preserved**, reinstalling replaces only the block, and
`--uninstall` removes it and leaves the rest untouched.

## Status-bar hook

By default, speaking prints a status line in chat each time. Two coding agents can instead
show live progress in their own status bar — verified against their actual settings
schemas, not assumed:

| Agent | Mechanism |
| --- | --- |
| Claude Code | `~/.claude/settings.json` → `statusLine.command`, with a real `refreshInterval` timer (1–60s) |
| Qwen Code | `~/.qwen/settings.json` → `ui.statusLine.command`, same idea |

```bash
tts hooks                # what's detected, installed, and why the rest can't do this
tts hooks --install      # install into every detected supported agent
tts hooks --status       # is a hook live right now? (exit 0/1; used by the skill)
tts hooks --uninstall    # remove it, restoring whatever status line was there before
```

```
$ tts hooks
supported : claude-code, qwen

[ok] claude-code  active
[  ] qwen         agent not detected

[xx] codex        not supported: no status line mechanism yet (open feature request upstream)
[xx] copilot      not supported: has one, but its config schema isn't documented solidly enough to target yet
[xx] cursor       not supported: would need a full VS Code extension, not a lightweight hook
[xx] gemini       not supported: footer settings are show/hide toggles only; no custom command
[xx] opencode     not supported: no status line mechanism yet (open feature request upstream)
[xx] windsurf     not supported: would need a full VS Code extension, not a lightweight hook
```

Only these two have a documented "run my command, show its stdout in the status bar"
mechanism today. Gemini CLI's footer is hide/show toggles only (checked its shipped
`settingsSchema.js`); Codex CLI and OpenCode both have open upstream feature requests for
this, not yet shipped; Cursor and Windsurf are VS Code forks where a status-bar item means
writing a real extension, not a lightweight hook; GitHub Copilot CLI has one, but its
config schema isn't documented solidly enough to target without an install to test against.

**Install never rewrites an existing status line — it appends into it.** If your settings
already point at a script (yours, or another tool's), that pointer is never touched;
instead a small block is added to the *end of that script file*, so the original tool keeps
owning its slot and keeps running exactly as it always did. Idle, output is byte-for-byte
what it was before — our block only adds text while something is actually playing:

```
$ tts hooks --install claude-code
  claude-code  did append into /home/user/.claude/statusline-command.sh -- your existing
               status line is untouched, and picks this up on its very next refresh
```

This only appends into a **plain path to a writable script file** — not a one-liner, not a
command with arguments, not something unwritable. If the existing command doesn't qualify,
install refuses and shows you the exact block to add by hand, or you can pass `--force` to
replace the pointer outright (the old chain-by-reference behavior — the existing command
still runs, but the tool that owned it no longer does, which is a real tradeoff, not a free
upgrade; only reach for it when appending genuinely isn't possible). Reinstalling replaces
our block in place rather than duplicating it. `--uninstall` removes only our block and
leaves the rest of the file untouched, or drops the settings key entirely if nothing was
configured before we installed.

Appended mode takes effect on the *very next status-bar refresh* — no restart needed, since
only the script's content changed, not anything Claude Code reads once at startup. A fresh
install with nothing configured before (or `--force`) does need a restart, since those set
`statusLine.command`/`refreshInterval` directly. When a hook is live, the `local-tts-speak`
skill stops printing its own status line — `tts hooks --status` is what it checks.

### Refresh cadence

With nothing else configured, a fresh install defaults to a real 2-second timer. When
appending into an *existing* status line, the existing refresh cadence — timer or
event-only — is left exactly as it was by default, since changing it also changes how often
the other tool's own script re-runs, not just ours:

```bash
tts hooks --install claude-code --refresh-interval 2   # a real timer, ticks live
tts hooks --install claude-code --refresh-interval 0   # explicitly event-based, no timer
tts hooks --install claude-code                        # leave whatever cadence was already there
```

`0` is a deliberate choice, not the same as omitting the flag — it removes `refreshInterval`
outright (so the status bar only redraws on host events like a new message), whereas
omitting the flag means "don't decide, leave it as configured." Changing the cadence
(anything other than "leave it as configured") does write to settings.json — just the
`refreshInterval` key, never `command` — so that one does need a restart.

### Multiple sessions

Running more than one session at once (two terminals, two agent instances) works without
one's audio stopping another's or its status bar showing the wrong progress. Pass
`--session` with anything that identifies the run:

```bash
tts -b --session "$CLAUDE_CODE_SESSION_ID" "hello"
tts stop --session "$CLAUDE_CODE_SESSION_ID"
```

Playback state is stored per session; starting playback only stops a *previous* playback
from the *same* session, and `stop`/`pause`/`resume`/`playback` only ever act on your own
session's audio. What two sessions *can't* do is talk over each other: actual audio output
is serialized machine-wide (see [Background playback](#background-playback)) — a second
session's `-b` call queues rather than overlapping, it just doesn't stop or otherwise touch
the first session's state while it waits. `--session` is auto-detected when omitted — currently from
`$CLAUDE_CODE_SESSION_ID`, verified by capturing a live status-line payload from Claude Code
and confirming it carries the exact same value in its `session_id` field, which is also how
the status-bar hook knows which session's progress to show. Omit `--session` entirely and
everything works exactly as before it existed — one shared slot.

## Language memory

Which backend speaks which language is remembered in the config file, so the preference
survives sessions and is shared by *every* agent rather than living in one agent's memory.

```bash
tts languages                                     # show what is recorded
tts languages --set es=piper:~/voices/es_MX.onnx  # provider + voice
tts languages --set en=llamacpp                   # provider only
tts languages --forget de
```

Then just name the language:

```bash
tts --lang es "Hola, ya terminé."
tts --lang es -f documento.md -o documento.wav
```

The lookup prefers the specific tag over the base one, so with both recorded, `--lang es-MX`
picks the Mexican entry while `--lang es` picks the generic one. Explicit flags always win
over the memory, and a recorded voice is only applied to the provider it was recorded for —
a piper `.onnx` is never handed to llama.cpp.

This is what the agent skills write to when you give feedback like *"use piper for
Spanish"* or *"that accent is wrong, use the Mexican voice"*. You can also set it per shell
with `LOCALTTS_LANG_ES=piper:/path/voice.onnx`.

## Providers

```bash
tts providers
```

### `llamacpp` — local, offline, four languages

Runs `llama-tts`. Zero configuration: with no model set it passes
`--tts-oute-default` and llama.cpp handles the weights.

| Setting | Default | Description |
| --- | --- | --- |
| `binary` | `llama-tts` | Path to or name of the executable. |
| `model` | *(empty)* | TTS GGUF. Empty means "use the default OuteTTS weights". |
| `vocoder` | *(empty)* | WavTokenizer GGUF. **Required whenever `model` is set.** |
| `hf_repo` / `hf_file` | *(empty)* | Pull the TTS model from Hugging Face instead. |
| `hf_repo_vocoder` / `hf_file_vocoder` | *(empty)* | Same, for the vocoder. |
| `speaker_file` | *(empty)* | Voice profile JSON (`--tts-speaker-file`). |
| `max_words` | `26` | Words per prompt; longer text is split and re-joined. `0` disables. |
| `threads` | `0` | CPU threads; `0` lets llama.cpp decide. |
| `gpu_layers` | `null` | Layers to offload (`-ngl`); `null` keeps llama.cpp's default. |
| `guide_tokens` | `true` | `--tts-use-guide-tokens`, improves word recall. |
| `extra_args` | `[]` | Extra flags appended verbatim. |

Output is 24 kHz mono WAV. The default OuteTTS weights speak **English, Chinese,
Japanese and Korean**; other languages come out with English phonetics, so use the
[`piper`](#piper--small-fast-offline-many-languages) provider for those. Quality also
drops on long prompts, which is why `max_words` splits them — raise or lower it to trade
continuity against reliability.

#### Using your own models

```bash
tts config --set llamacpp.model=~/models/OuteTTS-0.2-500M-Q8_0.gguf
tts config --set llamacpp.vocoder=~/models/WavTokenizer-Large-75-F16.gguf
```

Or fetch them from Hugging Face at run time:

```bash
tts config --set llamacpp.hf_repo=OuteAI/OuteTTS-0.2-500M-GGUF
tts config --set llamacpp.hf_file=OuteTTS-0.2-500M-Q8_0.gguf
tts config --set llamacpp.hf_repo_vocoder=ggml-org/WavTokenizer
tts config --set llamacpp.hf_file_vocoder=WavTokenizer-Large-75-F16.gguf
```

Speed it up with your own hardware settings:

```bash
tts -s threads=8 -s gpu_layers=99 "offloaded to the GPU"
```

### `openai` — any OpenAI-compatible endpoint

Speaks HTTP (`POST /v1/audio/speech`) using `urllib` — no SDK involved. It works
with OpenAI itself and with local servers such as
[openedai-speech](https://github.com/matatonic/openedai-speech),
[Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI), or LocalAI.

| Setting | Default |
| --- | --- |
| `base_url` | `https://api.openai.com/v1` |
| `api_key` | *(empty — falls back to `$OPENAI_API_KEY`)* |
| `model` | `tts-1` |
| `voice` | `alloy` |
| `speed` | `1.0` |
| `timeout` | `120` |
| `tone` | *(empty)* |
| `auto_tone` | `false` |

`tone` is flat voice-style instructions sent with every call (`model=gpt-4o-mini-tts` only);
`auto_tone` derives tone from `?`/`!`/`.` where no `<tag>` is active in the text. See
[Tone and emotion tags](#tone-and-emotion-tags) below.

```bash
export OPENAI_API_KEY=sk-...
tts -p openai -v nova "Hello from the cloud."

# a local server needs no key at all
tts config --set openai.base_url=http://localhost:8880/v1
tts -p openai -o out.mp3 "Local, but OpenAI-shaped."
```

This is the only provider that writes formats other than WAV — the output
extension picks the format (`wav`, `mp3`, `opus`, `aac`, `flac`, `pcm`).

### `piper` — small, fast, offline, many languages

[Piper](https://github.com/OHF-Voice/piper1-gpl) runs neural ONNX voices on the CPU at
roughly 7x realtime, with good models for ~40 languages. Use it when `llamacpp` does not
cover your language: the default OuteTTS weights handle English, Chinese, Japanese and
Korean only, and will read anything else with English phonetics.

Piper is distributed as a Python wheel (`piper-tts`, GPL-3.0). Install it in its **own**
virtualenv so its ~200 MB of dependencies (onnxruntime, numpy) stay out of this project,
then point `local-tts` at the binary:

```bash
python -m venv ~/.local/share/piper-venv
~/.local/share/piper-venv/bin/pip install piper-tts

# list every voice, then fetch the one you want (~60 MB for "medium", ~63 MB for "high")
mkdir -p ~/.local/share/piper-voices && cd ~/.local/share/piper-voices
~/.local/share/piper-venv/bin/python -m piper.download_voices            # list
~/.local/share/piper-venv/bin/python -m piper.download_voices es_MX-claude-high

tts config --set piper.binary=~/.local/share/piper-venv/bin/piper
tts config --set piper.model=~/.local/share/piper-voices/es_MX-claude-high.onnx
tts -p piper "Piper es muy rápido en una CPU."
```

Voice weights live at [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices)
(MIT/CC, no account or API token needed). Naming is `<lang>_<REGION>-<speaker>-<quality>`,
where quality is `x_low`, `low`, `medium`, or `high`.

Piper splits long input into sentences by itself, so an entire document works in one call:

```bash
tts -p piper -f article.md -o article.wav
```

| Setting | Default | Description |
| --- | --- | --- |
| `binary` | `piper` | Path to or name of the executable. |
| `model` | *(empty)* | Path to a `.onnx` voice. Required. |
| `speaker` | `null` | Speaker id for multi-speaker voices. |
| `length_scale` | `null` | Phoneme length (inverse of rate); `null` uses piper's own default. |
| `volume` | `null` | Volume multiplier; `null` uses piper's own default. |
| `auto_tone` | `false` | Derive tone from `?`/`!`/`.` where no `<tag>` is active — see [Tone and emotion tags](#tone-and-emotion-tags). |
| `extra_args` | `[]` | Extra flags appended verbatim. |

### `kokoro` — small, fast, offline, many languages

An alternative to piper with similar footprint and language coverage (Kokoro-82M). There
is no single official CLI, so the straightforward path is a minimal wrapper around the
`kokoro`/`kokoro-onnx` Python package, in its own venv:

```bash
python -m venv ~/.local/share/kokoro-venv
~/.local/share/kokoro-venv/bin/pip install kokoro-onnx soundfile

mkdir -p ~/.local/share/kokoro-models && cd ~/.local/share/kokoro-models
# fetch kokoro-v1.0.onnx and voices-v1.0.bin, e.g. from
# https://github.com/nazdridoy/kokoro-tts/releases
```

Save this as `~/.local/share/kokoro-venv/kokoro_cli.py`:

```python
#!/usr/bin/env python3
import argparse, os, sys, warnings
warnings.filterwarnings("ignore")
MODELS = os.path.expanduser("~/.local/share/kokoro-models")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("-o", "--output", required=True)
    p.add_argument("-v", "--voice", default="af_heart")
    p.add_argument("-l", "--lang", default="en-us")
    p.add_argument("-s", "--speed", type=float, default=1.0)
    p.add_argument("text", nargs="*")
    a = p.parse_args()
    text = " ".join(a.text).strip() or sys.stdin.read().strip()
    if not text:
        sys.exit("kokoro-tts: no text")
    import soundfile as sf
    from kokoro_onnx import Kokoro
    k = Kokoro(f"{MODELS}/kokoro-v1.0.onnx", f"{MODELS}/voices-v1.0.bin")
    samples, rate = k.create(text, voice=a.voice, speed=a.speed, lang=a.lang)
    sf.write(a.output, samples, rate)

if __name__ == "__main__":
    main()
```

And a shim on `PATH` so `kokoro.binary`'s default (`kokoro-tts`) finds it:

```bash
mkdir -p ~/.local/bin
cat > ~/.local/bin/kokoro-tts <<'EOF'
#!/usr/bin/env bash
exec "$HOME/.local/share/kokoro-venv/bin/python" \
     "$HOME/.local/share/kokoro-venv/kokoro_cli.py" "$@"
EOF
chmod +x ~/.local/bin/kokoro-tts
```

Then configure a voice — IDs are per-language, e.g. `af_heart` for US English, `ef_dora`
for Spanish:

```bash
tts config --set kokoro.voice=ef_dora
tts config --set kokoro.lang=es
tts -p kokoro "Prueba de voz con Kokoro."
```

| Setting | Default | Description |
| --- | --- | --- |
| `binary` | `kokoro-tts` | Path to or name of the executable. |
| `model_dir` | *(empty)* | Optional. Only for a kokoro CLI that resolves model files by working directory (e.g. `nazdridoy/kokoro-tts`) rather than managing them internally like the wrapper above. |
| `voice` | *(empty)* | Voice id. Empty uses the binary's own default. |
| `lang` | *(empty)* | Language code. Empty uses the binary's own default. |
| `speed` | `1.0` | Playback speed multiplier. |
| `auto_tone` | `false` | Derive tone from `?`/`!`/`.` where no `<tag>` is active — see [Tone and emotion tags](#tone-and-emotion-tags). Speed only; kokoro has no volume knob. |
| `extra_args` | `[]` | Extra flags appended verbatim. |
| `server_url` | *(empty)* | Optional. See [Optional: a persistent server](#optional-a-persistent-server-kokoro--rvc) below. |
| `server_start`, `server_timeout` | *(empty)*, `30` | Command to auto-start the server, and how long to wait for it. |

#### Per-language voices

Kokoro names every voice by language — `a`/`b` English, `e` Spanish, `f` French, and so
on — so one flat `voice` cannot serve two languages. Map them instead:

```bash
tts config --set kokoro.language_voices.en=bm_george   # British male
tts config --set kokoro.language_voices.es=ef_dora     # Spanish female
```

An exact tag beats its base language (`es-MX` before `es`), and the phonemizer language
is taken from the chosen voice rather than from `lang` — otherwise switching languages
leaves a stale `lang` behind and one language gets read with another's phonetics.

#### Emphasis, and kokoro's own pauses

```bash
tts config --set kokoro.emphasis_lengthen=2   # IPA length marks on the stressed vowel
tts config --set kokoro.sentence_pause=0.25   # kokoro's own within-utterance pauses
tts config --set kokoro.clause_pause=0.1
```

`emphasis_lengthen` is emphasis the way a phonetician writes it: N length marks on the
vowel carrying primary stress, `kˈasa` → `kˈaːsa`. Kokoro has `ː` in its own vocabulary,
so the model hears it — an isolated word measures 0.576s plain, 0.640s with one mark,
0.661s with two. It needs the persistent server, which is where the phonemizer lives, and
`0` disables it.

### `rvc` — voice conversion (not installed automatically)

[rvc-python](https://github.com/daswer123/rvc-python) does **audio-to-audio voice
conversion only** — it has no text input. This provider chains it: synthesize with another
provider first (`rvc.base_provider`, piper by default), then convert that result to a
target voice with a trained model.

**Never installed automatically** — it pulls in `torch` and is sizable. Set it up
deliberately:

```bash
python -m venv ~/.local/share/rvc-venv
~/.local/share/rvc-venv/bin/pip install rvc-python
# GPU support needs a matching torch build; see the rvc-python README for the index URL.

tts config --set rvc.python=~/.local/share/rvc-venv/bin/python
tts config --set rvc.model=~/.local/share/rvc-models/<name>/<name>.pth
tts config --set rvc.index=~/.local/share/rvc-models/<name>/<index-file>   # optional
tts config --set rvc.base_provider=piper

tts -p rvc --dry-run "test"    # shows both steps: base synthesis, then conversion
tts -p rvc "Test of the converted voice."
```

| Setting | Default | Description |
| --- | --- | --- |
| `python` | *(empty)* | Path to the interpreter in the venv rvc-python is installed in. Required. |
| `base_provider` | *(empty)* | Which provider synthesizes the base voice. Empty uses the overall default provider. |
| `model` | *(empty)* | Path to a `.pth` voice model. Required. |
| `index` | *(empty)* | Optional `.index` file; improves quality. |
| `device` | `cpu` | `cpu` or `cuda:0`, matching the torch build installed. |
| `pitch` | `0` | Semitone shift. |
| `method` | *(empty)* | Pitch extraction algorithm: `harvest`, `crepe`, `rmvpe`, `pm`. Empty uses rvc-python's default. |
| `index_rate`, `protect` | *(empty)* | Passed through to rvc-python when set. |
| `extra_args` | `[]` | Extra flags appended verbatim. |
| `server_url` | *(empty)* | Optional. See [Optional: a persistent server](#optional-a-persistent-server-kokoro--rvc) below. |
| `server_start`, `server_timeout` | *(empty)*, `60` | Command to auto-start the server, and how long to wait for it (a torch load is slower than kokoro's). |

There is no `rvc.voice` — the voice comes entirely from which `.pth` model is configured.

### Optional: a persistent server (kokoro / rvc)

Both providers above reload their model from disk on every call by default. If that's slow
enough to matter, either can instead talk to a small persistent server that loads the model
once and serves requests over `localhost` — this tool never runs that server itself, it's a
short script you write into the provider's own venv (the `local-tts-configure` skill has
the exact script for both, self-contained, stdlib `http.server` only):

```bash
tts config --set kokoro.server_url=http://127.0.0.1:8765
tts config --set 'kokoro.server_start=~/.local/share/kokoro-venv/bin/python ~/.local/share/kokoro-venv/kokoro_server.py --port 8765'
tts -p kokoro "test"     # auto-starts it on first use (a few seconds), fast after that
```

Auto-start polls `server_url`'s `/health` until it answers or `server_timeout` elapses,
then the request goes through as an HTTP POST instead of a subprocess call — voice,
language and speed still travel per request, not fixed to whatever the server started
with. `tts check` reports whether it's running or will auto-start, without starting it
itself. **It exits on its own after 5 minutes idle** to release the model (a real cost for
rvc's torch model in particular) — configurable via `--idle-timeout SECONDS` in
`server_start` (`0` disables it); the next call after it exits just starts a fresh one.

For `rvc`, the model is fixed at server startup (`--model`/`--index` in `server_start`),
not per request — that's inherent to keeping one loaded; switching voices means changing
those and restarting the server.

### Tone and emotion tags

Wrap a stretch of text in `<name>...</name>` to mark its tone, e.g.
`<happy>Good news!</happy> <serious>One thing needs your review.</serious>`. Any word works
as a tag name; a built-in preset exists for common ones (anger, happy, joy, sad, fear,
surprise, disgust, calm, excited, serious, whisper, sarcastic, urgent, gentle, confident,
tired, playful, question, exclamation — see `TAG_PROFILES` in `src/localtts/text.py`), and
anything else still works with a generic phrase, just without a hand-tuned preset. Tags can
nest (`<serious><question>...</question></serious>` combines both) and escape a literal
angle bracket with `\<` / `\>`.

```bash
tts -p openai --model gpt-4o-mini-tts "<happy>Good news!</happy> <serious>One thing needs your review.</serious>"
tts -p piper "<whisper>Very quiet now.</whisper> Back to normal."
```

What a tag actually does depends on the backend, since not every one has a real hook for
it — see each provider's own table above (`openai.tone`/`auto_tone`, `piper.auto_tone`,
`kokoro.auto_tone`) and the `local-tts-configure` skill for the full breakdown. On a backend
with no hook at all (the default `llamacpp`, `rvc`), a tag is a safe no-op: it's always
stripped before the text is spoken, never read out literally.

### `command` — anything else

An escape hatch for any binary that can write a WAV file. `{text}` and `{output}`
are substituted as single argv items, so text is never re-parsed by a shell.

```bash
tts config --set 'command.template=espeak-ng -w {output} {text}'
tts -p command "Whatever tool you like."

# macOS
tts config --set 'command.template=say -o {output} --data-format=LEI16@22050 {text}'
```

By default, a `<tag>` is stripped before `{text}` is filled in, like any provider above with
no real tone hook. If your own script is written to parse the markup itself, opt in with
`tts config --set command.tone_tags=pass`.

Also unlike every other provider, local-tts does **not** reshape what your command
produced. Elsewhere its capabilities are known, so a tag's leftover speed and volume are
safely applied to the rendered audio; here the script is yours and may already be varying
its own delivery. Opt in only if it doesn't:

```bash
tts config --set command.audio_fx=true
```

If you wired up something through `command` that now has a real provider above (kokoro,
rvc), `tts config --detect-migrations` finds it and prints the exact `--set` commands to
switch — it never applies them, and never touches `command.template` itself:

```bash
$ tts config --detect-migrations
command.template runs something tts now supports natively as `kokoro`:
  command.template runs 'kokoro-tts', which local-tts now supports natively
  tts config --set kokoro.voice=ef_dora
  tts config --set kokoro.lang=es
```

---

## Configuration

Settings are resolved in this order, later winning:

```
built-in defaults  <  config file  <  environment variables  <  CLI flags
```

### Config file

```bash
tts config --path      # where it lives
tts config --show      # the effective configuration, defaults included
tts config --init      # write a file containing every default, ready to edit
```

Default location:

| Platform | Path |
| --- | --- |
| Linux / macOS | `~/.config/local-tts/config.json` |
| Windows | `%APPDATA%\local-tts\config.json` |
| any, if `XDG_CONFIG_HOME` is set | `$XDG_CONFIG_HOME/local-tts/config.json` |

`$LOCALTTS_CONFIG` overrides the path entirely. The file only needs to contain what you
change:

```json
{
  "provider": "llamacpp",
  "play": true,
  "providers": {
    "llamacpp": {
      "threads": 8,
      "gpu_layers": 99
    }
  }
}
```

Write to it from the CLI:

```bash
tts config --set provider=piper
tts config --set llamacpp.threads=8
tts config --set play=false
```

Top-level keys are `provider`, `play` (play by default when no `--output`), and
`player` (force a playback command). Everything else is `<provider>.<key>`.

### Environment variables

| Variable | Effect |
| --- | --- |
| `LOCALTTS_CONFIG` | Use a different config file path. |
| `LOCALTTS_PROVIDER` | Default provider. |
| `LOCALTTS_PLAY` | `true`/`false`. |
| `LOCALTTS_PLAYER` | Playback command. |
| `LOCALTTS_<PROVIDER>_<KEY>` | Any provider setting, e.g. `LOCALTTS_LLAMACPP_THREADS=8`. |
| `OPENAI_API_KEY` | Fallback key for the `openai` provider. |

### Per-run overrides

`-s/--set` changes a setting for one invocation only:

```bash
tts -s threads=4 "just this once"
tts -p openai -s model=tts-1-hd -s speed=1.15 "faster, nicer"
```

---

### Language tags — a borrowed word, said properly

Real speech mixes languages. `"Ya subí el pull request"` read entirely with Spanish
phonetics sounds wrong, because "pull request" is English. Tag the span and it is
synthesized with that language's voice:

```console
$ tts --lang es "Ya subí el <en>pull request</en> al repositorio."
```

The Spanish parts use the Spanish voice, the tagged span uses the English one — and with
`rvc` **all of it still converts to the same target voice**, so it stays one character
speaking, just with the right phonetics for each word.

```bash
tts config --set 'rvc.delivery.es={"pause_ms": 45, "language_tags": true}'
```

Enabled per language, so it can be on for Spanish and off elsewhere. Tagging a language
requires that language to be configured, so add a voice for it first.

Which voice speaks the borrowed span is configurable per **host** language, because the
best voice for a quoted English phrase inside Spanish is not always the one you would
pick to narrate a whole English paragraph — a closer timbre matters more mid-sentence:

```bash
tts config --set 'rvc.delivery.es={"language_tags": true, "foreign_voices": {"en": "bm_lewis"}}'
```

Without an override it falls back to the base provider's own per-language voice. `<lang:en>` works
too, as do region tags (`<es-MX>`). Only languages you have actually configured count as
language tags — otherwise a tone tag nobody anticipated would silently become a language
switch. Tone tags straddling a language boundary are re-balanced across the cut, so
`<calm>Hola <en>hello</en> adios</calm>` still reads calmly throughout.

### Pronunciation dictionary

Say these words this way. Applied before synthesis on every backend, so a name or a piece
of jargon comes out right no matter which voice is speaking:

```bash
tts config --set pronunciations.jarvis="JAR-viss"
tts config --set pronunciations.kubectl="cube cuddle"
tts config --set pronunciations.es:jarvis="yarvis"   # Spanish only
tts config --set pronunciations.jarvis=                # empty value removes it
```

Keys match whole words, case-insensitively; the replacement is used exactly as written,
because a respelling's own capitalization is often load-bearing. A bare key applies to
every language, and `<lang>:<word>` applies to that one only — so a word said differently
in two languages needs no nested structure. Tone-tag markup is left untouched: an entry
for `happy` will not rewrite `<happy>`.

### Delivery: pacing and pauses

How each language is delivered, on top of whatever a tone tag asks for:

```bash
tts config --set 'rvc.delivery.es={"speed": 1.0, "pause_ms": 45, "pause_tone_ms": 130, "emphasis_lengthen": 2}'
tts config --set 'rvc.delivery.en={"speed": 1.0, "pause_ms": 60, "pause_tone_ms": 160}'
```

| Key | Meaning |
| --- | --- |
| `speed` | Rate multiplier, folded into the base provider's own rate control |
| `pause_ms` | Silence between fragments delivered the same way |
| `pause_tone_ms` | Silence where the tone changes — the breath a speaker takes |
| `emphasis_lengthen` | IPA length marks on the stressed vowel (kokoro base only) |

`"*"` applies to any language not named. Spanish runs faster with shorter gaps than
English, which is why this is per-language rather than one number — and why the previous
behavior, a hardcoded 350 ms between every fragment, read as a stall rather than a breath.

The pause is padded onto the fragment itself rather than inserted while joining, so
streamed playback and the saved file are the same sound.

## Audio playback

There is no audio library to install.

| Platform | Player |
| --- | --- |
| **Windows** | PowerShell's built-in sound player — nothing to install |
| **macOS** | `afplay`, built in |
| **Linux** | the first of `ffplay` → `paplay` → `aplay` → `play` → `mpv` → `cvlc` |
| **WSL** | a Linux player if present, otherwise it reaches out to Windows automatically |

```bash
sudo apt install ffmpeg     # Debian/Ubuntu
brew install ffmpeg         # macOS

tts --player mpv "use this one instead"
tts config --set player=ffplay
```

If nothing is found, the file is kept and its path printed instead of vanishing.

**On WSL, installing ffmpeg does not change your player.** Windows' own player is the
default on both Windows and WSL, because it is always present and it is the native way
out of either — and WSL's Linux audio bridge is frequently the noisier of the two, so
letting a freshly-installed `ffplay` take over would be a silent downgrade. Name a Linux
player explicitly if you want one:

```bash
tts config --set player=ffplay      # or mpv, paplay, ...
tts config --set player=windows     # back to Windows' own player
```

### Tuning a player for one machine

Audio stacks differ per box, and the fix is nearly always a flag or an environment
variable rather than a code change — so both are configuration:

```bash
tts config --set 'player_args.ffplay=-af aresample=48000'   # inserted before the file
tts config --set player_env.SDL_AUDIODRIVER=pulseaudio      # set for the player only
tts config --set player_args.ffplay=                        # empty value removes it
```

`player_args` is keyed by player name and inserted just before the file argument;
`player_env` is applied to the player process alone, so nothing leaks into your shell
profile. `tts check` echoes both back when set.

**ffmpeg is worth installing even if you already have a player.** Tone tags change a
span's pacing, and on any backend without its own rate control that retiming happens to
the rendered audio: with ffmpeg through `atempo`, without it through a built-in WSOLA
stretch that is listenable but measurably noisier. `tts check` prints a `tone shaping:`
line naming which one you are getting.

### Streaming playback

Each fragment plays as soon as it is synthesized, rather than the whole text being
rendered and joined first — so the first words arrive in about a second instead of after
however long the full text takes. The joined file is still written either way.

```bash
tts config --set stream=false    # render everything first, then play one file
tts --no-stream "..."            # same, for one run only
```

Fragment boundaries are the tone-tag and chunk boundaries that already existed; nothing is
split that was not split before. Playback stays serialized machine-wide, and the runner
holds the lock across the whole stream, so another session cannot cut in mid-sentence.

---

## Troubleshooting

**`llama-tts: 'llama-tts' not found on PATH`**
Install llama.cpp, or point at the binary: `tts config --set llamacpp.binary=/full/path/to/llama-tts`.

**`llamacpp.model is set but llamacpp.vocoder is not`**
`llama-tts` needs two files: the TTS model *and* the WavTokenizer vocoder. Set both,
or clear `model` (`tts config --set llamacpp.model=`) to fall back to the defaults.

**`llamacpp can only write .wav files`**
Only the `openai` provider produces other formats. Render WAV, then convert:
`ffmpeg -i out.wav out.mp3`.

**"no audio player found"**
Install `ffmpeg`, or use `--output` and open the file yourself.

**First run hangs for a long time**
It is downloading ~640 MB of weights. Run with `--verbose` to watch the progress.

**The backend failed and I want to know why**
`--verbose` streams the backend's own stderr; `--dry-run` prints the exact command
so you can run it by hand.

---

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .

python -m unittest discover -s tests -v   # no test dependencies either
```

Layout:

```
src/localtts/
├── cli.py            argument parsing and the four subcommands
├── config.py         defaults, config file, env vars, precedence
├── text.py           markdown stripping and sentence-aware chunking
├── audio.py          playback autodetection and wav joining
├── skills.py         agent detection and skill installation
├── agent_skills/     the skill markdown shipped to agents
├── errors.py         TTSError -> a clean one-line message
└── providers/
    ├── base.py       Provider contract + subprocess helpers
    ├── llamacpp.py   llama.cpp backend
    ├── openai.py     OpenAI-compatible HTTP
    ├── piper.py      Piper ONNX voices
    └── command.py    user-defined template
```

Adding a provider: subclass `Provider`, implement `synthesize(text, out_path, voice)`
and `check()`, register it in `providers/__init__.py`, and add its defaults to
`config.DEFAULTS["providers"]`. A test asserts those two stay in sync.

## License

MIT
