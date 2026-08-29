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

## The six backends

| Backend | Offline | Languages | Needs |
| --- | --- | --- | --- |
| `llamacpp` (default) | yes | **English, Chinese, Japanese, Korean only** | `llama-tts` binary |
| `piper` | yes | ~40 languages, fast | `piper` binary + a `.onnx` voice |
| `kokoro` | yes | ~40 languages, small model | a `kokoro-tts` wrapper (set up below) |
| `openai` | no | whatever the endpoint offers | a URL, and a key only for api.openai.com |
| `rvc` | yes | inherits its base provider's | **not installed automatically** — see below |
| `command` | yes | whatever the tool offers | any binary that writes a WAV |

**The single most common problem:** the user's text is not in one of llamacpp's four
languages, so it is pronounced with English phonetics. The fix is piper, not a setting.

**If the user has an existing `command.template`** wired to something that now has a real
provider above (kokoro, rvc), run `tts config --detect-migrations` and offer to switch —
full workflow in the `local-tts-update` skill, since that's checked on every update too.

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

## Adding Kokoro (small, fast, offline, many languages)

Kokoro-82M is a comparable alternative to piper — similar footprint, similar language
coverage. Reach for it when the user specifically wants Kokoro, or when piper's available
voices don't cover what they need.

There is no single official CLI to install; the simplest reliable path is a minimal
wrapper around the `kokoro`/`kokoro-onnx` Python package, kept in its own venv the same
way piper is:

```bash
python3 -m venv ~/.local/share/kokoro-venv
~/.local/share/kokoro-venv/bin/pip install kokoro-onnx soundfile

mkdir -p ~/.local/share/kokoro-models && cd ~/.local/share/kokoro-models
# fetch kokoro-v1.0.onnx and voices-v1.0.bin -- ask the user before downloading;
# they're published at https://github.com/nazdridoy/kokoro-tts/releases
```

Write the wrapper script (`~/.local/share/kokoro-venv/kokoro_cli.py`):

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

And a thin shell shim on PATH so `kokoro.binary`'s default (`kokoro-tts`) finds it:

```bash
mkdir -p ~/.local/bin
cat > ~/.local/bin/kokoro-tts <<'EOF'
#!/usr/bin/env bash
exec "$HOME/.local/share/kokoro-venv/bin/python" \
     "$HOME/.local/share/kokoro-venv/kokoro_cli.py" "$@"
EOF
chmod +x ~/.local/bin/kokoro-tts
```

Ask which voice and language the user wants — voice IDs are per-language (e.g. `af_heart`
for US English, `ef_dora` for Spanish; the underlying model card lists the full set) —
then register and record it the same way as piper:

```bash
tts config --set kokoro.voice=ef_dora
tts config --set kokoro.lang=es
tts languages --set es=kokoro
tts --lang es "Prueba de voz con Kokoro."
```

`kokoro.model_dir` is a separate, optional setting for a *different* kind of kokoro CLI
(one that resolves its model files from its own working directory rather than managing
them internally, like this wrapper does) — leave it unset for the setup above.

### Optional: keep the model loaded (a persistent server)

The CLI wrapper above reloads the whole model from disk on every single call. For someone
speaking frequently, that per-call cost adds up. If they ask for that to be faster, offer
this — **ask before setting it up**, it's an extra background process, not a routine step:

```bash
cat > ~/.local/share/kokoro-venv/kokoro_server.py <<'EOF'
#!/usr/bin/env python3
import argparse, io, json, os, sys, threading, time, warnings
from http.server import BaseHTTPRequestHandler, HTTPServer
warnings.filterwarnings("ignore")
MODELS = os.path.expanduser("~/.local/share/kokoro-models")

def make_handler(kokoro, last_activity):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            # not "activity" -- must not keep the process alive just because polled
            if self.path == "/health":
                self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
            else:
                self.send_response(404); self.end_headers()
        def do_POST(self):
            if self.path != "/synthesize":
                self.send_response(404); self.end_headers(); return
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            text = (body.get("text") or "").strip()
            if not text:
                self.send_response(400); self.end_headers(); return
            import soundfile as sf
            samples, rate = kokoro.create(text, voice=body.get("voice") or "af_heart",
                                          speed=float(body.get("speed") or 1.0),
                                          lang=body.get("lang") or "en-us")
            buf = io.BytesIO(); sf.write(buf, samples, rate, format="WAV")
            last_activity[0] = time.time()
            self.send_response(200); self.send_header("Content-Type", "audio/wav")
            self.end_headers(); self.wfile.write(buf.getvalue())
    return Handler

def watch_idle(last_activity, idle_timeout):
    if idle_timeout <= 0:
        return
    while True:
        time.sleep(10)
        if time.time() - last_activity[0] > idle_timeout:
            print("idle for %ds, exiting to release the model" % idle_timeout, file=sys.stderr)
            os._exit(0)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--idle-timeout", type=int, default=300,
                   help="exit after this many idle seconds; 0 disables")
    args = p.parse_args()
    from kokoro_onnx import Kokoro
    print("loading model...", file=sys.stderr)
    kokoro = Kokoro(f"{MODELS}/kokoro-v1.0.onnx", f"{MODELS}/voices-v1.0.bin")
    last_activity = [time.time()]
    threading.Thread(target=watch_idle, args=(last_activity, args.idle_timeout), daemon=True).start()
    print("ready on port %d (idle timeout %ds)" % (args.port, args.idle_timeout), file=sys.stderr)
    HTTPServer(("127.0.0.1", args.port), make_handler(kokoro, last_activity)).serve_forever()

if __name__ == "__main__":
    main()
EOF

tts config --set kokoro.server_url=http://127.0.0.1:8765
tts config --set 'kokoro.server_start=~/.local/share/kokoro-venv/bin/python ~/.local/share/kokoro-venv/kokoro_server.py --port 8765'
tts -p kokoro "Prueba con el servidor."   # auto-starts it (a few seconds, model load),
                                          # every call after that is fast
```

Voice, language and speed still travel **per call** — the server holding the model
resident doesn't fix them to whatever it started with. **It exits on its own after 5
minutes idle** (`--idle-timeout`, seconds — append e.g. `--idle-timeout 600` to
`kokoro.server_start` for a different value, or `--idle-timeout 0` to disable it and keep
it running indefinitely); the next call after it exits just auto-starts a fresh one, paying
the model-load cost again. Checked in ~10s increments, so actual shutdown can lag the
configured timeout by up to that much — irrelevant at the 5-minute default. A health check
(what auto-start's own readiness poll does) does not count as activity and never keeps it
alive on its own. `tts check` reports whether it's already running, or will auto-start on
first use, without starting it itself (a check
should never have the side effect of spinning up a background process). Nothing here is
run or managed by this tool beyond talking to it and auto-starting it — killing the
process is how you stop it; the next call starts a fresh one.

## Adding RVC (voice conversion — not installed automatically)

RVC (retrieval-based voice conversion) does **not** do text-to-speech. `rvc-python`
converts an existing audio file to a target voice; it has no text input at all. The `rvc`
provider handles this by chaining: it synthesizes with another provider first (piper by
default, or whatever `rvc.base_provider` names), then converts that result to the target
voice. This is for a specific, deliberate ask — "make it sound like this voice" with a
trained `.pth` model in hand — not a general voice-quality upgrade; point most requests at
piper or kokoro instead.

**Always ask before installing.** rvc-python pulls in torch and is sizable — never install
it as a side effect of a routine request.

```bash
python3 -m venv ~/.local/share/rvc-venv
~/.local/share/rvc-venv/bin/pip install rvc-python
# GPU support needs a matching torch build -- see https://github.com/daswer123/rvc-python
# for the exact index URL; ask the user whether they have a GPU before adding it.
```

The user needs an actual trained voice model — a `.pth` file, plus an optional `.index`
file that improves quality — from wherever they trained or downloaded one (out of scope
here; this skill only wires up whatever they already have).

```bash
tts config --set rvc.python=~/.local/share/rvc-venv/bin/python
tts config --set rvc.model=~/.local/share/rvc-models/<name>/<name>.pth
tts config --set rvc.index=~/.local/share/rvc-models/<name>/<index-file>     # optional
tts config --set rvc.base_provider=piper     # or kokoro/llamacpp -- whichever gives the
                                              # best base voice to convert from
tts -p rvc --dry-run "test"    # shows both steps: base synthesis, then conversion
tts -p rvc "Test of the converted voice."
```

`rvc.device` defaults to `cpu`; set it to `cuda:0` only if the venv above actually has a
CUDA-enabled torch installed. There is no `rvc.voice` — voice comes entirely from which
`.pth` model is configured, not a per-call flag.

### Optional: keep the model loaded (a persistent server)

RVC's model load (torch, plus the checkpoint itself) is the slow part of every call — a
persistent server pays that cost once instead of per call. **Ask before setting this up**,
same as always for anything that adds a background process:

```bash
cat > ~/.local/share/rvc-venv/rvc_server.py <<'EOF'
#!/usr/bin/env python3
import argparse, json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer

def make_handler(rvc, last_activity):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            # not "activity" -- must not keep the process alive just because polled
            if self.path == "/health":
                self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
            else:
                self.send_response(404); self.end_headers()
        def do_POST(self):
            if self.path != "/convert":
                self.send_response(404); self.end_headers(); return
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            input_path = body.get("input_path") or ""
            if not input_path or not os.path.exists(input_path):
                self.send_response(400); self.end_headers(); return
            if "pitch" in body:
                rvc.set_params(f0up_key=int(body["pitch"]))
            out_path = input_path + ".converted.wav"
            rvc.infer_file(input_path, out_path)
            data = open(out_path, "rb").read()
            os.unlink(out_path)
            last_activity[0] = time.time()
            self.send_response(200); self.send_header("Content-Type", "audio/wav")
            self.end_headers(); self.wfile.write(data)
    return Handler

def watch_idle(last_activity, idle_timeout):
    if idle_timeout <= 0:
        return
    while True:
        time.sleep(10)
        if time.time() - last_activity[0] > idle_timeout:
            print("idle for %ds, exiting to release the model" % idle_timeout, file=sys.stderr)
            os._exit(0)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--model", required=True)
    p.add_argument("--index", default="")
    p.add_argument("--device", default="cpu")
    p.add_argument("--idle-timeout", type=int, default=300,
                   help="exit after this many idle seconds; 0 disables")
    args = p.parse_args()
    from rvc_python.infer import RVCInference
    print("loading model...")
    rvc = RVCInference(device=args.device)
    rvc.load_model(args.model, index_path=args.index)
    last_activity = [time.time()]
    threading.Thread(target=watch_idle, args=(last_activity, args.idle_timeout), daemon=True).start()
    print("ready on port %d (idle timeout %ds)" % (args.port, args.idle_timeout))
    HTTPServer(("127.0.0.1", args.port), make_handler(rvc, last_activity)).serve_forever()

if __name__ == "__main__":
    main()
EOF

tts config --set rvc.server_url=http://127.0.0.1:8766
tts config --set 'rvc.server_start=~/.local/share/rvc-venv/bin/python ~/.local/share/rvc-venv/rvc_server.py --port 8766 --model ~/.local/share/rvc-models/<name>/<name>.pth --index ~/.local/share/rvc-models/<name>/<index-file>'
tts -p rvc "Test with the server."   # auto-starts it (torch load, several seconds);
                                     # every call after that is faster
```

**The model is fixed at server startup**, not per request — that's inherent to keeping one
loaded. `rvc.model`/`rvc.index` still configure the CLI fallback path, but the running
server keeps whichever model it was launched with; to switch voices, change
`rvc.server_start`'s `--model`/`--index` and restart the server (kill the process — the
next call auto-starts a fresh one with the new arguments). `tts check` reports whether it's
already running or will auto-start, without starting it itself.

Same idle behavior as kokoro's server: **exits after 5 minutes with no conversion
request** (`--idle-timeout`, seconds — append e.g. `--idle-timeout 600` to
`rvc.server_start` for a different value, `0` to disable). Worth knowing here specifically
because a torch model held resident uses real memory (and VRAM on `cuda:0`) the whole time
it's up — the default releases that automatically rather than leaving it loaded forever.

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

Playback is also serialized machine-wide — at most one file plays at a time, regardless of
provider or session, via a lock file `play_detached()` holds for the duration of playback
(see `audio.playback_lock_path()`, `localtts/_playback_runner.py`). If the user reports
"nothing plays" or "it's stuck at 0:00", check whether *another session* already has audio
running (`tts playback --session <other-id>` if known, or just ask) — a queued session
correctly shows frozen `0:00 / <total>` until its turn comes, that is not a bug. It starts
advancing once the earlier one finishes.

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
