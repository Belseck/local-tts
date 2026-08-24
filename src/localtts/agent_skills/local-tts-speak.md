---
name: local-tts-speak
description: Speak text out loud to the user with the local `tts` CLI (local-tts). Use when the user asks you to talk to them, read something aloud, narrate a file, say something, or produce an audio version of text — including in another language ("háblame", "léeme esto", "lis-moi ça"). Runs fully offline; picks the right backend for the language automatically.
---

# Speaking to the user with `local-tts`

The `tts` command turns text into speech locally. Use it whenever the user wants to
**hear** something rather than read it.

## When to use this

Triggers include: "talk to me", "say that out loud", "read this file to me", "narrate
this", "can you speak?", "make an audio of this", "háblame", "léeme esto", "lies mir das
vor". Also use it when the user has asked you to *keep* talking to them — once they ask for
voice, prefer voice for your substantive answers until they say otherwise.

Do **not** use it for ordinary text replies the user did not ask to hear.

## Before the first use

```bash
command -v tts || echo "local-tts is not installed"
tts check
```

`tts check` prints every backend and marks the usable ones `[ok]`. If `tts` is missing, or
the backend for the language you need is not `[ok]`, switch to the
`local-tts-configure` skill instead of improvising.

## Choosing the language

**Always check the recorded preferences first.** They are the user's own past feedback:

```bash
tts languages
```

Then speak with the language flag, which selects the remembered backend and voice:

```bash
tts --lang es "Hola, ya terminé la tarea."
tts --lang en "Done — the tests pass."
```

If the language you need is **not** recorded, do not guess silently. Pick a sane default,
say which one you used, and offer to remember it:

- The default `llamacpp` backend only speaks **English, Chinese, Japanese and Korean**.
  Anything else comes out with English phonetics and sounds wrong.
- For any other language, `piper` is the right backend — but it needs a voice for that
  language installed. If none is, use the `local-tts-configure` skill.

## The default way to speak: `-b`

**Always use `--background` (`-b`).** It synthesizes, starts playback, and returns
immediately — you are never blocked for the length of the audio, and the user can pause or
stop it while you carry on working. It also keeps the file and prints its path.

```bash
tts -b --lang es "Terminé de revisar el código."
```

Prints the path on stdout and the controls on stderr:

```
playing in the background (pid 4123) — `tts stop` to end it
/tmp/local-tts-a1b2c3d4.wav
```

**Run the command itself in the background too**, using whatever mechanism you have for
non-blocking shell commands. `-b` returns as soon as playback starts, but *synthesis* still
happens first, and that takes a moment (a second or two for a sentence, ~12s for a
1000-word document). Never sit blocked on it.

### Always play the whole thing

**Play regardless of length.** Do not decide for the user that something is too long to
listen to, do not truncate, and do not silently switch to a summary. If they asked to hear
a document, play the document.

The only exceptions are when the user says otherwise — "just save it", "don't play it",
"give me the file" — or when there is no audio player at all. Then use `--no-play` and hand
them the path.

### Letting the user pause and stop

Because playback is detached, the user stays in control. **Tell them these once, the first
time you speak in a session**, then do not repeat them every turn:

```bash
tts pause      # suspend it
tts resume     # continue
tts stop       # end it
tts playback   # is anything playing? paused or playing, and which file
```

Run these immediately when the user asks — "stop", "pause", "shut up", "para", "silencio"
all mean run the command now, before answering anything else. Starting a new playback
stops the previous one automatically, so you never stack two voices.

`pause` and `resume` are POSIX-only (Linux, macOS, WSL). On native **Windows** they report
that they are unsupported — there, `stop` is the control, and you should say so rather than
suggesting pause.

### Always tell the user where the file is

Every `-b` run prints the path. **Say it in your reply**, every time — the user may want to
replay it, keep it, or send it on:

> Playing now — saved at `/tmp/local-tts-a1b2c3d4.wav`. Say "stop" or "pause" any time.

Default to a temp folder, which is what `-b` does on its own (`/tmp` on Linux/macOS,
`%TEMP%` on Windows). Only write elsewhere when the user asks for a specific location:

```bash
tts -b --lang es -o ~/audio/resumen.wav -f resumen.md     # their location, still plays
```

Temp files survive until the OS clears them, so a path you printed stays valid for the rest
of the session. Do not delete it yourself unless asked.

## Other commands

```bash
# read a file aloud; markdown syntax is stripped automatically for .md
tts -b --lang es -f notas.md

# the user asked for a file and no playback
tts --no-play --lang es -f notas.md -o notas.wav

# blocking playback, only if you deliberately want to wait for it to finish
tts --lang es "corto"
```

Long documents need no special handling: markdown is stripped for `.md` sources, and
backends that need short prompts get the text split at sentence boundaries and rejoined
into one file.

## Remembering the user's feedback

This is the important part. When the user reacts to a voice — **write it down** so every
agent and every future session benefits:

| The user says | Run |
| --- | --- |
| "use piper for Spanish" | `tts languages --set es=piper` |
| "use this voice for Spanish" | `tts languages --set es=piper:/path/to/voice.onnx` |
| "that accent is wrong, use the Mexican one" | `tts languages --set es-MX=piper:/path/to/es_MX-....onnx` |
| "English is fine as it is" | `tts languages --set en=llamacpp` |
| "stop using piper for German" | `tts languages --forget de` |

Confirm briefly after recording it ("noted — Spanish will use piper from now on"), then use
`--lang` for that language from then on. Never keep a language preference only in your own
context: the config file is the shared memory, and other agents read it too.

If the user's complaint is about **quality** rather than language (too fast, wrong gender,
robotic), that is a voice choice — list the alternatives with the
`local-tts-configure` skill and let them pick, then record the winner the same way.

## Platform notes

The `tts` commands above are identical on **Linux, macOS and Windows** — only the shell
quoting differs.

| | Linux / macOS | Windows (PowerShell) |
| --- | --- | --- |
| Run it | `tts --lang es "hola"` | `tts --lang es "hola"` |
| Check it exists | `command -v tts` | `Get-Command tts` |
| Quoting | single or double quotes | double quotes; `` ` `` escapes, not `\` |
| Paths in arguments | `~/notes.md` | `"$HOME\notes.md"` or a full path |

- On **Windows** playback uses PowerShell's built-in sound player, so it works with nothing
  installed.
- On **macOS** it uses `afplay`, also built in.
- On **Linux** it needs one of `ffplay`, `paplay`, `aplay`, `play`, `mpv` or `cvlc`. If
  `tts check` reports `players : none found`, always pass `-o file.wav` and tell the user
  the path instead of trying to play.
- In **WSL**, playback reaches out to Windows automatically. Write output under the Linux
  filesystem or `/mnt/c/...`, and convert Windows paths the user gives you:
  `C:\Users\x\a.md` becomes `/mnt/c/Users/x/a.md`.

Do not assume a shell. If the user is on Windows, do not emit `&&`-chained POSIX one-liners;
run the commands one at a time.

## Practicalities

- **Without `-b`, playback blocks** until the audio finishes. That is occasionally what you
  want; `-b` is the default choice.
- **Write what you say.** Print the text you are speaking as well, so the user has it if
  they miss a word or the audio fails.
- **Compose for the ear when the words are yours.** Speech runs ~150 words per minute, so
  keep your *own* spoken replies tight and skip code, paths and punctuation-heavy output.
  This is about what you choose to say — it is never a reason to shorten or skip text the
  user explicitly asked you to read.
- **Never read secrets aloud** — tokens, keys, passwords — even if they appear in the text
  you were asked to narrate. Skip them and say you did.
- Speech is written to a temp file and deleted after playing, unless `-o` or `--keep`.
