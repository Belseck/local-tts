"""Status-bar hooks: install a script into a coding agent's own "run my command and show
its stdout in the status bar" mechanism, so playback progress lives in the IDE/terminal
chrome instead of chat messages.

Only agents with a REAL, documented mechanism of that shape are supported. Verified by
reading each agent's own settings schema / shipped source, not by assumption:

  claude-code   ~/.claude/settings.json -> statusLine.command (+ refreshInterval, a real
                timer, not just per-turn) -- confirmed against a live installation.
  qwen          ~/.qwen/settings.json   -> ui.statusLine.command (+ refreshInterval) --
                confirmed against the official Qwen Code docs.

Everything else in skills.AGENTS is a documented non-match, not an unresearched gap:

  gemini        footer settings are hide/show toggles only (checked the shipped
                settingsSchema.js) -- no arbitrary command.
  codex         open feature request (openai/codex#17827, #20140, #20244); not shipped.
  opencode      open feature request (#30295); community plugins exist but hook into
                OpenCode's own plugin API, not a simple external command.
  cursor,
  windsurf      VS Code forks -- a status-bar item there means writing and installing a
                real extension, not a lightweight config hook.
  copilot       does have a real statusLine.command (per public write-ups), but the
                settings path/schema is not documented officially and one source notes
                "field names vary by CLI version" -- not solid enough to write into a
                real config file unverified. Left unsupported until confirmed firsthand.

A settings file's statusLine slot is exclusive -- only one command can occupy it. If the
agent already has one (the user's own, or another tool's), install() WRAPS it rather than
replacing it: our script runs the previous command first and only adds our text when
something is actually playing, so an idle system looks exactly like it did before we
touched it. uninstall() restores exactly that prior command.
"""

import json
import os
import shutil
import stat
import time
from pathlib import Path

from localtts.errors import TTSError

MARKER_START = "# >>> local-tts statusline hook (managed by `tts hooks`) — do not edit by hand"
MARKER_END = "# <<< local-tts statusline hook"

HEARTBEAT_MAX_AGE = 20  # seconds; must exceed every supported agent's max refreshInterval

#: name -> (settings file relative to home, key path into that JSON, extra literal keys
#: to set alongside "type"/"command", default refreshInterval)
HOOK_AGENTS = {
    "claude-code": (".claude/settings.json", ("statusLine",), {"padding": 0}, 2),
    "qwen": (".qwen/settings.json", ("ui", "statusLine"), {"hideContextIndicator": False}, 2),
}

#: Every other agent skills.py knows about, with why it can't get a hook today. Shown by
#: `tts hooks` so "not supported" reads as a finding, not silence.
UNSUPPORTED = {
    "gemini": "footer settings are show/hide toggles only; no custom command",
    "codex": "no status line mechanism yet (open feature request upstream)",
    "opencode": "no status line mechanism yet (open feature request upstream)",
    "cursor": "would need a full VS Code extension, not a lightweight hook",
    "windsurf": "would need a full VS Code extension, not a lightweight hook",
    "copilot": "has one, but its config schema isn't documented solidly enough to target yet",
}


def home(base=None):
    return Path(base) if base else Path.home()


def hook_dir(base=None):
    return home(base) / ".local" / "share" / "local-tts" / "hooks"


def wrapper_path(agent, base=None):
    return hook_dir(base) / ("%s-statusline.sh" % agent)


def heartbeat_path(agent, base=None):
    return hook_dir(base) / ("%s.heartbeat" % agent)


def settings_path(agent, base=None):
    relative, _, _, _ = HOOK_AGENTS[agent]
    return home(base) / relative


def _read_json(path):
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise TTSError("%s is not valid JSON: %s" % (path, exc))
    return data if isinstance(data, dict) else {}


def _get_nested(data, key_path):
    node = data
    for key in key_path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _set_nested(data, key_path, value):
    node = data
    for key in key_path[:-1]:
        node = node.setdefault(key, {})
    node[key_path[-1]] = value


def _pop_nested(data, key_path):
    node = data
    for key in key_path[:-1]:
        if not isinstance(node, dict) or key not in node:
            return
        node = node[key]
    if isinstance(node, dict):
        node.pop(key_path[-1], None)


def _shell_single_quote(text):
    return "'" + text.replace("'", "'\"'\"'") + "'"


def _existing_previous_command(wrapper_file):
    """The PREV_CMD this wrapper already carries, if we wrote it before. Reading it back
    (instead of only checking settings.json) is what makes reinstall idempotent: once
    we own the slot, settings.json's command IS our wrapper path, so the only place the
    real previous command still lives is inside the file we wrote last time."""
    if not wrapper_file.exists():
        return None
    for line in wrapper_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("PREV_CMD="):
            raw = line[len("PREV_CMD="):]
            if raw in ("''", '""'):
                return None
            return raw[1:-1].replace("'\"'\"'", "'") if raw.startswith("'") else raw
    return None


def _render_wrapper(agent, previous_command, base=None):
    prev_literal = _shell_single_quote(previous_command) if previous_command else "''"
    heartbeat = heartbeat_path(agent, base)
    return "\n".join([
        "#!/usr/bin/env bash",
        MARKER_START,
        "PREV_CMD=%s" % prev_literal,
        "input=\"$(cat)\"",
        "mkdir -p %s 2>/dev/null" % _shell_single_quote(str(heartbeat.parent)),
        "date +%%s > %s 2>/dev/null" % _shell_single_quote(str(heartbeat)),
        "bar=\"\"",
        "if command -v tts >/dev/null 2>&1; then",
        # $input carries the host's own JSON (it has a session_id field on every agent we
        # support), so this hook shows only THIS session's playback, not any session's.
        "  bar=\"$(printf '%s' \"$input\" | tts playback --compact 2>/dev/null)\"",
        "fi",
        "prev=\"\"",
        "if [ -n \"$PREV_CMD\" ]; then",
        "  prev=\"$(printf '%s' \"$input\" | eval \"$PREV_CMD\" 2>/dev/null)\"",
        "fi",
        "if [ -n \"$bar\" ]; then",
        "  if [ -n \"$prev\" ]; then printf '%s · %s' \"$bar\" \"$prev\"; "
        "else printf '%s' \"$bar\"; fi",
        "else",
        "  printf '%s' \"$prev\"",
        "fi",
        MARKER_END,
        "",
    ])


def detect(names=None, base=None):
    """Which supported agents are actually installed, as {name: config path}."""
    from localtts import skills   # local import: skills.py doesn't need to know about hooks
    installed = skills.detect(base)
    names = names or HOOK_AGENTS
    return {name: installed[name] for name in names if name in installed}


def install(agent, base=None, refresh_interval=None, dry_run=False):
    if agent not in HOOK_AGENTS:
        if agent in UNSUPPORTED:
            raise TTSError("%s has no status-line hook to install: %s" % (agent, UNSUPPORTED[agent]))
        raise TTSError("unknown agent %r" % agent)

    _, key_path, extra, default_interval = HOOK_AGENTS[agent]
    path = settings_path(agent, base)
    data = _read_json(path)
    current = _get_nested(data, key_path) or {}
    wrapper_file = wrapper_path(agent, base)

    if isinstance(current, dict) and current.get("command") == str(wrapper_file):
        previous = _existing_previous_command(wrapper_file)   # reinstall: carry the chain forward
    else:
        previous = current.get("command") if isinstance(current, dict) else None

    script = _render_wrapper(agent, previous, base)
    block = {"type": "command", "command": str(wrapper_file),
             "refreshInterval": refresh_interval or default_interval}
    block.update(extra)

    if dry_run:
        return {"settings_path": path, "wrapper_path": wrapper_file,
                "chained_from": previous, "block": block}

    wrapper_file.parent.mkdir(parents=True, exist_ok=True)
    wrapper_file.write_text(script, encoding="utf-8")
    wrapper_file.chmod(wrapper_file.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    _set_nested(data, key_path, block)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    return {"settings_path": path, "wrapper_path": wrapper_file, "chained_from": previous, "block": block}


def uninstall(agent, base=None):
    if agent not in HOOK_AGENTS:
        raise TTSError("unknown agent %r" % agent)
    _, key_path, _, _ = HOOK_AGENTS[agent]
    path = settings_path(agent, base)
    wrapper_file = wrapper_path(agent, base)
    data = _read_json(path)
    current = _get_nested(data, key_path) or {}

    if not (isinstance(current, dict) and current.get("command") == str(wrapper_file)):
        return {"removed": False, "settings_path": path}   # not ours to touch

    previous = _existing_previous_command(wrapper_file)
    if previous:
        _set_nested(data, key_path, {"type": "command", "command": previous})
    else:
        _pop_nested(data, key_path)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    if wrapper_file.exists():
        wrapper_file.unlink()
    hb = heartbeat_path(agent, base)
    if hb.exists():
        hb.unlink()
    return {"removed": True, "settings_path": path, "restored": previous}


def is_installed(agent, base=None):
    if agent not in HOOK_AGENTS:
        return False
    _, key_path, _, _ = HOOK_AGENTS[agent]
    data = _read_json(settings_path(agent, base))
    current = _get_nested(data, key_path) or {}
    return isinstance(current, dict) and current.get("command") == str(wrapper_path(agent, base))


def is_active(agent, base=None, max_age=HEARTBEAT_MAX_AGE):
    """Installed AND actually being invoked right now by a live host session -- distinct
    from is_installed(), since a hook can be configured in an agent that isn't the one
    currently running. Freshness of the heartbeat file the wrapper touches on every call
    is what tells us a host is live, not just configured."""
    hb = heartbeat_path(agent, base)
    if not hb.exists():
        return False
    try:
        stamp = float(hb.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    return (time.time() - stamp) <= max_age


def any_active(base=None, max_age=HEARTBEAT_MAX_AGE):
    """Is *some* installed hook live right now? What the speak skill actually needs to
    decide whether to print a chat status line -- not which specific agent it is."""
    return any(is_active(name, base, max_age) for name in HOOK_AGENTS)
