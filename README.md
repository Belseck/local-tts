# local-tts

A tiny command-line text-to-speech tool. It shells out to **llama.cpp's `llama-tts`**
by default, so speech is generated locally and offline.

```console
$ tts "Hello from my terminal."
$ echo "Read this out loud." | tts
$ tts -f chapter1.txt -o chapter1.wav
```

**Zero runtime dependencies.** The package installs nothing but itself — no
`requests`, no `numpy`, no audio libraries. Everything is Python's standard library
plus binaries you already have (or install once, on your terms).

---

## Contents

- [Requirements](#requirements)
- [Install](#install)
- [Quick start](#quick-start)
- [Usage](#usage)
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
</details>

### Verify the install

```bash
tts check
```

```
config file : /home/you/.config/local-tts/config.json (not created yet)
default     : llamacpp

[ok] llamacpp  /usr/local/bin/llama-tts -> default OuteTTS (downloaded on first run)
[--] openai    https://api.openai.com/v1 (no api_key and $OPENAI_API_KEY is unset)
[--] piper     piper: 'piper' not found on PATH. ...
[ok] command   espeak-ng -w {output} {text}

players     : ffplay, paplay
```

Only the line matching your default provider has to say `[ok]`.

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

# see the exact llama.cpp command without running it
tts --dry-run "hello"
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
| `-o, --output FILE` | Write the audio here instead of playing it. |
| `-p, --provider NAME` | `llamacpp` (default), `openai`, `piper`, `command`. |
| `-v, --voice VOICE` | Speaker file (llamacpp), `.onnx` voice (piper), or voice name (openai). |
| `-m, --model MODEL` | Override the provider's model for this run. |
| `-s, --set KEY=VALUE` | Override any provider setting for this run. Repeatable. |
| `--play` | Play the audio *and* keep `--output`. |
| `--no-play` | Never play; just report the file path. |
| `--player CMD` | Force a playback command instead of autodetecting. |
| `--keep` | Keep the temporary file and print its path. |
| `--dry-run` | Print the backend command that would run, then exit. |
| `--verbose` | Show the backend's own (noisy) output. |
| `--version` | Print the version. |

Input precedence is `TEXT` → `--file` → stdin. Without `--output`, audio goes to a
temporary file that is played and then deleted (`--keep` keeps it).

Exit codes: `0` success, `1` error (with a one-line message on stderr), `130` interrupted.

---

## Providers

```bash
tts providers
```

### `llamacpp` — default, local, offline

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
| `threads` | `0` | CPU threads; `0` lets llama.cpp decide. |
| `gpu_layers` | `null` | Layers to offload (`-ngl`); `null` keeps llama.cpp's default. |
| `guide_tokens` | `true` | `--tts-use-guide-tokens`, improves word recall. |
| `extra_args` | `[]` | Extra flags appended verbatim. |

Output is 24 kHz mono WAV.

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

```bash
export OPENAI_API_KEY=sk-...
tts -p openai -v nova "Hello from the cloud."

# a local server needs no key at all
tts config --set openai.base_url=http://localhost:8880/v1
tts -p openai -o out.mp3 "Local, but OpenAI-shaped."
```

This is the only provider that writes formats other than WAV — the output
extension picks the format (`wav`, `mp3`, `opus`, `aac`, `flac`, `pcm`).

### `piper` — small, fast, offline

Uses [Piper](https://github.com/rhasspy/piper) ONNX voices. Download a voice from
[rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices), then:

```bash
tts config --set piper.model=~/voices/en_US-lessac-medium.onnx
tts -p piper "Piper is very fast on a CPU."
```

| Setting | Default | Description |
| --- | --- | --- |
| `binary` | `piper` | Path to or name of the executable. |
| `model` | *(empty)* | Path to a `.onnx` voice. Required. |
| `speaker` | `null` | Speaker id for multi-speaker voices. |
| `extra_args` | `[]` | Extra flags appended verbatim. |

### `command` — anything else

An escape hatch for any binary that can write a WAV file. `{text}` and `{output}`
are substituted as single argv items, so text is never re-parsed by a shell.

```bash
tts config --set 'command.template=espeak-ng -w {output} {text}'
tts -p command "Whatever tool you like."

# macOS
tts config --set 'command.template=say -o {output} --data-format=LEI16@22050 {text}'
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

Default location `~/.config/local-tts/config.json`
(`$XDG_CONFIG_HOME/local-tts/config.json` if set, or `$LOCALTTS_CONFIG` to override
the path entirely). It only needs to contain what you change:

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

## Audio playback

There is no audio library to install. The first available player wins:

`ffplay` (ffmpeg) → `paplay` → `aplay` → `afplay` (macOS) → `play` (sox) → `mpv` → `cvlc`.

On WSL with no Linux player installed, it falls back to Windows' own
`powershell.exe` sound player.

```bash
sudo apt install ffmpeg     # Debian/Ubuntu
brew install ffmpeg         # macOS

tts --player mpv "use this one instead"
tts config --set player=ffplay
```

If nothing is found, the file is kept and its path printed instead of vanishing.

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
├── audio.py          playback autodetection
├── errors.py         TTSError -> a clean one-line message
└── providers/
    ├── base.py       Provider contract + subprocess helpers
    ├── llamacpp.py   default backend
    ├── openai.py     OpenAI-compatible HTTP
    ├── piper.py      Piper ONNX voices
    └── command.py    user-defined template
```

Adding a provider: subclass `Provider`, implement `synthesize(text, out_path, voice)`
and `check()`, register it in `providers/__init__.py`, and add its defaults to
`config.DEFAULTS["providers"]`. A test asserts those two stay in sync.

## License

MIT
