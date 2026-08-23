# Agent install guide for `local-tts`

**Audience: an AI coding agent.** A human has asked you to install this tool on their
machine. Follow this document top to bottom. It is self-contained — you do not need to
read the rest of the repository first.

The human triggers you with something like:

> "Install local-tts."
> "I want to install this with the link."
> "Set this up with piper in Spanish, and make it global."

---

## Rules

1. **Never install anything, download a model, create a symlink, or run `sudo` without
   asking first.** Every step below marked **ASK** is a stop point. Present what you are
   about to do, wait for a yes.
2. **Detect before you ask.** Run the detection commands first so your questions are about
   what is actually missing, not about everything.
3. **Batch your questions.** Ask once, up front, covering every decision you found — do
   not interrupt the human six times.
4. **Report honestly.** If a step fails, say so and stop; do not paper over it or claim
   success. If you skip something, say what and why.
5. **Do not modify the user's shell config** (`.bashrc`, `.zshrc`, `PATH` exports) unless
   they explicitly ask. Prefer the symlink approach in Step 5.

### Consent shortcuts

Map the human's phrasing onto the questions so you do not re-ask what they already
answered:

| If they said… | Treat as answered |
| --- | --- |
| "with the link", "make it global", "on my PATH" | Step 5 (symlink) = **yes** |
| "with piper", "for Spanish/French/German/…", "for <language>" | Step 4 (piper) = **yes** |
| "just the CLI", "no extras" | Steps 4 and 5 = **no** |
| "install everything", "all of it", "don't ask" | All steps = **yes**, but still show the plan before running |

Anything they did not cover, you still ask.

---

## Step 0 — Detect the current state

Run these and keep the results. None of them change anything.

```bash
# repo + python
pwd && ls pyproject.toml 2>/dev/null && echo "repo: ok"
python3 --version                       # need >= 3.9
python3 -c "import ensurepip" 2>&1      # if this errors, venv creation will fail

# is local-tts already installed?
command -v tts && tts --version

# backends
command -v llama-tts && llama-tts --version 2>&1 | head -1
command -v piper || ls ~/.local/share/piper-venv/bin/piper 2>/dev/null

# audio players (any one is enough)
for p in ffplay paplay aplay afplay play mpv cvlc; do command -v $p; done
grep -qi microsoft /proc/version 2>/dev/null && echo "WSL: powershell.exe fallback available"

# where a symlink would go
echo "$PATH" | tr ':' '\n' | grep -E "\.local/bin|/usr/local/bin"
ls -la ~/.local/bin/tts 2>/dev/null && echo "WARNING: ~/.local/bin/tts already exists"
```

Summarize for the human as a short table: **present / missing** for each of python, venv
support, llama.cpp, piper, an audio player.

**ASK now**, in one message, only about what is missing or undecided:

- Install llama.cpp? (needed for the default provider — skip only if they will use piper or openai exclusively)
- Install piper, and for which language/voice?
- Create the global symlink?
- Install an audio player if none was found?

---

## Step 1 — Python environment

Requires **Python ≥ 3.9**.

If `import ensurepip` failed in Step 0, `python3 -m venv` will not work. On Debian/Ubuntu
that means `python3-venv` is missing. Two ways out — **ASK** which:

```bash
# a) system package (needs sudo)
sudo apt install python3-venv

# b) no sudo: use a pyenv interpreter if one exists
ls ~/.pyenv/versions
~/.pyenv/versions/<VERSION>/bin/python -m venv .venv
```

Otherwise:

```bash
cd <repo>
python3 -m venv .venv
.venv/bin/python -V          # confirm it is the interpreter you expect
```

> If a `.venv` already exists but is broken (a failed earlier attempt), `rm -rf .venv` and
> recreate it — a half-built venv keeps a `bin/python` symlink to the wrong interpreter.

---

## Step 2 — Install the package

The project metadata uses PEP 639 licence fields, which need **pip ≥ 24.2** and
**setuptools ≥ 77**. Upgrade pip first or the install fails with
`configuration error: project.license must be string`.

```bash
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -e .
.venv/bin/tts --version
```

`local-tts` itself has **zero runtime dependencies** — if pip installs anything besides
`local-tts`, something is wrong; stop and report it.

---

## Step 3 — llama.cpp (default provider) — **ASK**

The default provider shells out to `llama-tts`. This repo neither bundles nor builds
llama.cpp.

```bash
# macOS / Linux
brew install llama.cpp

# Windows
winget install llama.cpp

# Prebuilt binaries: https://github.com/ggml-org/llama.cpp/releases
#   -> llama-<build>-bin-<platform>.zip, unzip, put the bin/ dir on PATH

# From source
git clone https://github.com/ggml-org/llama.cpp && cd llama.cpp
cmake -B build && cmake --build build --config Release -j
# binaries land in build/bin
```

Verify, and register a non-PATH location if needed:

```bash
llama-tts --version
# only if it is not on PATH:
.venv/bin/tts config --set llamacpp.binary=/full/path/to/llama-tts
```

**Model weights are downloaded on first use, not now** (~640 MB of OuteTTS + WavTokenizer
into `~/.cache/huggingface/hub`). Tell the human the first run will be slow.

⚠️ **Language limit — surface this proactively.** The default OuteTTS weights speak
**English, Chinese, Japanese and Korean only**. Any other language is rendered with
English phonetics and sounds wrong. If the human mentioned another language, recommend
Step 4 instead of treating it as optional.

---

## Step 4 — piper (other languages, faster) — **ASK**

Piper covers ~40 languages and runs ~7× realtime on CPU.

Two upstreams; know the difference before you answer questions about it:

| | `rhasspy/piper` | `OHF-Voice/piper1-gpl` ← **use this** |
| --- | --- | --- |
| Licence | MIT | **GPL-3.0** |
| Status | archived, last release Nov 2023 | actively maintained |
| Ships as | standalone binary | Python wheel (`piper-tts`) |

**Install it in its own virtualenv.** Piper pulls in onnxruntime and numpy (~200 MB);
putting those in this project's `.venv` would destroy its zero-dependency property. The
`piper.binary` setting exists precisely so the two stay separate.

```bash
python3 -m venv ~/.local/share/piper-venv
~/.local/share/piper-venv/bin/pip install piper-tts
```

Pick a voice — **ASK** which language/region if they have not said:

```bash
# list every available voice
~/.local/share/piper-venv/bin/python -m piper.download_voices

mkdir -p ~/.local/share/piper-voices && cd ~/.local/share/piper-voices
~/.local/share/piper-venv/bin/python -m piper.download_voices <VOICE>
```

Voice names are `<lang>_<REGION>-<speaker>-<quality>`, quality ∈ `x_low|low|medium|high`
(a `high` voice is ~60–65 MB). Weights come from
[`rhasspy/piper-voices`](https://huggingface.co/rhasspy/piper-voices) — public, MIT/CC, no
API token or account. Prefer the region that matches the human's audience, e.g. `es_MX`
for Latin American Spanish vs `es_ES` for Castilian.

Register it:

```bash
.venv/bin/tts config --set piper.binary=~/.local/share/piper-venv/bin/piper
.venv/bin/tts config --set piper.model=~/.local/share/piper-voices/<VOICE>.onnx
```

**ASK** whether piper should become the default provider (right answer if their content is
mostly not English):

```bash
.venv/bin/tts config --set provider=piper
```

---

## Step 5 — Global symlink ("the link") — **ASK**

This is what "install this with the link" refers to. It makes `tts` work in any directory
without activating the venv, because the entry point's shebang is an absolute path to the
venv's interpreter.

Check the target is free and the directory is on `PATH` **before** creating it:

```bash
echo "$PATH" | tr ':' '\n' | grep -q "$HOME/.local/bin" && echo "on PATH"
ls -la ~/.local/bin/tts 2>/dev/null   # must not already exist
```

- If `~/.local/bin` is **not** on `PATH`: report it and ask before touching shell config;
  do not silently edit `.bashrc`/`.zshrc`.
- If `~/.local/bin/tts` **already exists**: stop and show the human what it points to. Do
  not overwrite it without explicit permission.

```bash
mkdir -p ~/.local/bin
ln -s "$(pwd)/.venv/bin/tts" ~/.local/bin/tts
# optional second name:
ln -s "$(pwd)/.venv/bin/local-tts" ~/.local/bin/local-tts
```

Tell them the consequences:

- The install is **editable**, so edits to `src/localtts/` take effect immediately.
- **Do not move or delete the repo** — the symlink and shebang both point into it.
- Uninstall the link with `rm ~/.local/bin/tts`.

---

## Step 6 — Audio player — **ASK if none was found**

No audio library is installed by this package; playback uses whatever CLI player exists:
`ffplay` → `paplay` → `aplay` → `afplay` → `play` → `mpv` → `cvlc`. On WSL with none of
them, it falls back to Windows' `powershell.exe` player automatically.

```bash
sudo apt install ffmpeg     # Debian/Ubuntu (needs sudo — ask)
brew install ffmpeg         # macOS
```

Without a player, synthesis still works: the file is kept and its path printed instead of
being played.

---

## Step 7 — Validate

Run all of these and show the real output:

```bash
tts --version                 # or .venv/bin/tts if no symlink was created
tts                           # with no args, prints the full parameter list
tts check                     # per-provider readiness + detected players
```

`tts check` must show `[ok]` on the row for the **default provider**. Other rows may be
`[--]`; that is fine and expected (e.g. `openai` with no API key).

Then a real synthesis, in the human's language:

```bash
tts --no-play -o /tmp/localtts-smoke.wav "Hello, the installation works."
python3 -c "import wave; w=wave.open('/tmp/localtts-smoke.wav'); \
print('%d Hz, %.1fs' % (w.getframerate(), w.getnframes()/w.getframerate()))"
rm -f /tmp/localtts-smoke.wav
```

A non-zero duration means the pipeline works end to end. Optionally run the test suite:

```bash
.venv/bin/python -m unittest discover -s tests
```

---

## Step 8 — Report

Close with a short summary containing:

- What you installed, and **where** (repo `.venv`, `~/.local/share/piper-venv`, voices dir)
- What you skipped and why
- Whether the symlink was created, and the exact path
- The `tts check` result
- The one command they will use day to day, e.g.
  `tts -p piper -f documento.md -o documento.wav`
- How to undo it: `rm ~/.local/bin/tts`, `rm -rf .venv ~/.local/share/piper-venv ~/.local/share/piper-voices`

---

## Known failure modes

| Symptom | Cause | Fix |
| --- | --- | --- |
| `ensurepip is not available` | Debian/Ubuntu without `python3-venv` | `sudo apt install python3-venv`, or use a pyenv interpreter |
| `.venv/bin/python -V` reports an unexpected version | leftover `.venv` from a failed run | `rm -rf .venv` and recreate |
| `configuration error: project.license must be string` | pip/setuptools too old for PEP 639 | `pip install --upgrade pip`, then reinstall |
| pip installs numpy/onnxruntime into the project venv | piper installed in the wrong environment | undo; use a separate venv per Step 4 |
| Speech is in the right words but the wrong accent | OuteTTS used for a language it does not support | switch to piper (Step 4) |
| `tts` not found after the symlink | `~/.local/bin` not on `PATH` | report it; ask before editing shell config |
| `command not found: <binary>` from `tts` | a configured provider binary is missing | `tts check` names it; fix the path with `tts config --set <provider>.binary=…` |
| Traceback instead of a one-line error | a genuine bug | report it; the CLI is supposed to print `tts: error: …` |
