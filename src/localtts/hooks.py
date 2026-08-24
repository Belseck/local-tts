"""Status-bar hooks: get playback progress into a coding agent's own "run my command and
show its stdout in the status bar" mechanism, so it lives in the IDE/terminal chrome
instead of chat messages.

Only agents with a REAL, documented mechanism of that shape are supported. Verified by
reading each agent's own settings schema / shipped source, not by assumption:

  claude-code   ~/.claude/settings.json -> statusLine.command (+ refreshInterval, a real
                timer, not just per-turn) -- confirmed against a live installation.
  qwen          ~/.qwen/settings.json   -> ui.statusLine.command (+ refreshInterval) --
                confirmed against the official Qwen Code docs.

Everything else in skills.AGENTS is a documented non-match, not an unresearched gap --
see UNSUPPORTED below for the specific reason each one can't do this today.

A settings file's statusLine slot holds exactly one command. That command commonly belongs
to another tool already (a user's own script, or something like Boost) -- and that tool
may have its own installer that re-verifies or repairs its slot on update. Earlier this
module *replaced* the slot with its own wrapper and saved the previous command as a string
to restore later. That broke a real Boost installation: when Boost's own reinstall changed
what its script's path or contents were, our saved reference went stale, and because we now
owned the slot, Boost's installer no longer recognized it as its own -- so Boost quietly
stopped rendering, and the fix required reinstalling Boost by hand.

The fix is structural, not a patch: **install() never rewrites statusLine.command when one
is already configured.** Instead, if that command is a plain path to a writable script
file, we APPEND a delimited block to the END of that same file -- the same tool keeps
owning the settings.json slot and keeps running exactly as it did, and our text is simply
concatenated onto its output when something is playing. Nothing we do is visible to that
tool's own installer, because we never touched what it manages. Only when nothing was
configured at all do we write a standalone wrapper and take the (previously-empty) slot.
--force is available for a command we can't safely append to (not a bare file path, or not
writable) -- it works like before, but it is now something the caller opts into explicitly,
never the default.
"""

import json
import os
import shutil
import stat
import time
from pathlib import Path

from localtts.errors import TTSError

HOOK_BEGIN = "# >>> local-tts statusline hook (managed by `tts hooks`) — do not edit by hand"
HOOK_END = "# <<< local-tts statusline hook"

HEARTBEAT_MAX_AGE = 20  # seconds; must exceed every supported agent's max refreshInterval

#: name -> (settings file relative to home, key path into that JSON, extra literal keys
#: to set alongside "type"/"command" when we own the slot outright, default refreshInterval)
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


def _resolve_appendable_file(command, base=None):
    """If `command` is JUST a path to an existing, writable, plain-text file -- no
    arguments, no pipeline, no interpreter prefix -- return that Path. Anything more
    complex (a one-liner, a `node script.js`, a missing/read-only file) returns None:
    appending to those either can't work or can't be done safely, so the caller should
    fall back to asking for --force rather than guessing."""
    if not command or not isinstance(command, str):
        return None
    text = command.strip()
    if not text or any(ch.isspace() for ch in text):
        return None
    expanded_text = os.path.expanduser(text) if text.startswith("~") else text
    path = Path(expanded_text)
    if not path.is_absolute():
        return None
    try:
        if not path.is_file() or not os.access(path, os.W_OK):
            return None
    except OSError:
        return None
    return path


def _heartbeat_block(agent, base=None):
    """The text appended to an existing script, or the body of our own standalone one.
    Prints nothing extra when idle, so an idle status bar is byte-identical to before."""
    heartbeat = heartbeat_path(agent, base)
    return "\n".join([
        HOOK_BEGIN,
        "mkdir -p %s 2>/dev/null" % _shell_single_quote(str(heartbeat.parent)),
        "date +%%s > %s 2>/dev/null" % _shell_single_quote(str(heartbeat)),
        "if command -v tts >/dev/null 2>&1; then",
        # No stdin is threaded through here on purpose: appended mode runs after the
        # host's own script may have already consumed stdin, so we rely on session
        # auto-detection from the environment instead (see cli._resolve_session) --
        # the env var is set for the whole subprocess, not just what's piped to it.
        "  __localtts_bar=\"$(tts playback --compact 2>/dev/null)\"",
        "  if [ -n \"$__localtts_bar\" ]; then printf ' · %s' \"$__localtts_bar\"; fi",
        "fi",
        HOOK_END,
        "",
    ])


def _append_block(target_file, block):
    """Idempotent: replaces our own prior block in place if present, else appends once."""
    existing = target_file.read_text(encoding="utf-8") if target_file.exists() else ""
    if HOOK_BEGIN in existing and HOOK_END in existing:
        head, _, rest = existing.partition(HOOK_BEGIN)
        _, _, tail = rest.partition(HOOK_END)
        updated = head.rstrip("\n") + "\n" + block + tail.lstrip("\n")
    else:
        sep = "" if not existing or existing.endswith("\n") else "\n"
        updated = existing + sep + block
    target_file.write_text(updated, encoding="utf-8")


def _remove_block(target_file):
    if not target_file.exists():
        return False
    existing = target_file.read_text(encoding="utf-8")
    if HOOK_BEGIN not in existing or HOOK_END not in existing:
        return False
    head, _, rest = existing.partition(HOOK_BEGIN)
    _, _, tail = rest.partition(HOOK_END)
    remainder = head.rstrip("\n") + ("\n" if head.strip() else "") + tail.lstrip("\n")
    if remainder.strip():
        target_file.write_text(remainder, encoding="utf-8")
    else:
        target_file.unlink()
    return True


def _render_standalone_wrapper(agent, base=None):
    """Used only when nothing was configured before, or under --force: a self-contained
    script we fully own. previous_command (--force only) is chained via a saved literal,
    same as appending would do if we could -- the one path where that tradeoff is
    unavoidable, because the caller told us there's nothing safer to do."""
    return "\n".join(["#!/usr/bin/env bash", _heartbeat_block(agent, base).rstrip("\n"), ""])


def _render_forced_wrapper(agent, previous_command, base=None):
    """--force only: replace the slot outright, chaining the previous command by a saved
    reference. This is exactly the design that broke Boost when its own script moved --
    kept only as an explicit opt-in for a command too complex to append into."""
    prev_literal = _shell_single_quote(previous_command) if previous_command else "''"
    heartbeat = heartbeat_path(agent, base)
    return "\n".join([
        "#!/usr/bin/env bash",
        HOOK_BEGIN,
        "PREV_CMD=%s" % prev_literal,
        "input=\"$(cat)\"",
        "mkdir -p %s 2>/dev/null" % _shell_single_quote(str(heartbeat.parent)),
        "date +%%s > %s 2>/dev/null" % _shell_single_quote(str(heartbeat)),
        "bar=\"\"",
        "if command -v tts >/dev/null 2>&1; then",
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
        HOOK_END,
        "",
    ])


def detect(names=None, base=None):
    """Which supported agents are actually installed, as {name: config path}."""
    from localtts import skills   # local import: skills.py doesn't need to know about hooks
    installed = skills.detect(base)
    names = names or HOOK_AGENTS
    return {name: installed[name] for name in names if name in installed}


def install(agent, base=None, refresh_interval=None, dry_run=False, force=False):
    if agent not in HOOK_AGENTS:
        if agent in UNSUPPORTED:
            raise TTSError("%s has no status-line hook to install: %s" % (agent, UNSUPPORTED[agent]))
        raise TTSError("unknown agent %r" % agent)

    _, key_path, extra, default_interval = HOOK_AGENTS[agent]
    path = settings_path(agent, base)
    data = _read_json(path)
    current = _get_nested(data, key_path) or {}
    existing_command = current.get("command") if isinstance(current, dict) else None
    our_wrapper = wrapper_path(agent, base)

    # refresh_interval semantics, deliberately three-valued so "event-based" is something
    # the caller can ask for, not just what happens when they don't ask for anything:
    #   None -> don't decide: standalone mode still needs *some* cadence since we alone
    #           own the file, so it gets `default_interval`; appended mode leaves whatever
    #           was already configured exactly alone (the safe default from before).
    #   0    -> explicitly event-based: no refreshInterval key at all, in either mode.
    #   1-60 -> that many seconds, in either mode.

    # Nothing configured yet, or we already own the slot (a prior local-tts install) --
    # safe to write our own standalone wrapper and take the (empty-or-already-ours) slot.
    if not existing_command or existing_command == str(our_wrapper):
        script = _render_standalone_wrapper(agent, base)
        block = {"type": "command", "command": str(our_wrapper)}
        if refresh_interval != 0:
            block["refreshInterval"] = refresh_interval if refresh_interval is not None else default_interval
        block.update(extra)
        result = {"mode": "standalone", "settings_path": path, "target_file": our_wrapper,
                  "wrapper_path": our_wrapper, "block": block,
                  "refresh_interval": block.get("refreshInterval", 0)}
        if dry_run:
            return result
        our_wrapper.parent.mkdir(parents=True, exist_ok=True)
        our_wrapper.write_text(script, encoding="utf-8")
        our_wrapper.chmod(our_wrapper.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        _set_nested(data, key_path, block)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return result

    # Something else already owns the slot. Never touch settings.json's command --
    # append into the file it already points to, so that tool keeps managing itself.
    target = _resolve_appendable_file(existing_command, base)
    if target is not None and not force:
        block = _heartbeat_block(agent, base)
        # refreshInterval is the one exception to "never touch the existing block": it's
        # an independent timing knob, not part of what identifies who owns the slot (that
        # was always `command`, which we still never touch here). Still opt-in only --
        # bumping it also changes how often the OTHER tool's own script gets invoked, and
        # we have no visibility into whether that costs it anything.
        interval_note, settings_changed = None, False
        if refresh_interval == 0:
            interval_note = 0
            if "refreshInterval" in current:
                current = dict(current)
                current.pop("refreshInterval")
                settings_changed = True
        elif refresh_interval is not None:
            interval_note = refresh_interval
            if current.get("refreshInterval") != refresh_interval:
                current = dict(current)
                current["refreshInterval"] = refresh_interval
                settings_changed = True
        result = {"mode": "appended", "settings_path": path, "target_file": target,
                  "wrapper_path": None, "block": None, "refresh_interval": interval_note,
                  "settings_changed": settings_changed}
        if dry_run:
            return result
        _append_block(target, block)
        if settings_changed:
            _set_nested(data, key_path, current)
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return result

    if not force:
        raise TTSError(
            "statusLine.command is already %r, and it isn't a plain script file local-tts "
            "can safely extend (it has arguments, isn't absolute, or isn't writable). "
            "Add this to the end of it yourself:\n\n%s\n\nor re-run with --force to replace "
            "it outright -- that stops the existing command from running."
            % (existing_command, _heartbeat_block(agent, base))
        )

    # --force: explicit opt-in to the old behavior -- replace the slot, chaining the
    # previous command by saved reference. Fragile in the way that broke Boost, which is
    # exactly why it now requires the caller to ask for it.
    script = _render_forced_wrapper(agent, existing_command, base)
    block = {"type": "command", "command": str(our_wrapper),
             "refreshInterval": refresh_interval or default_interval}
    block.update(extra)
    result = {"mode": "forced", "settings_path": path, "target_file": our_wrapper,
              "wrapper_path": our_wrapper, "block": block, "chained_from": existing_command}
    if dry_run:
        return result
    our_wrapper.parent.mkdir(parents=True, exist_ok=True)
    our_wrapper.write_text(script, encoding="utf-8")
    our_wrapper.chmod(our_wrapper.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    _set_nested(data, key_path, block)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return result


def uninstall(agent, base=None):
    if agent not in HOOK_AGENTS:
        raise TTSError("unknown agent %r" % agent)
    _, key_path, _, _ = HOOK_AGENTS[agent]
    path = settings_path(agent, base)
    our_wrapper = wrapper_path(agent, base)
    data = _read_json(path)
    current = _get_nested(data, key_path) or {}
    command = current.get("command") if isinstance(current, dict) else None

    removed, detail = False, "nothing to remove (not installed by local-tts)"
    if command == str(our_wrapper):
        _pop_nested(data, key_path)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        if our_wrapper.exists():
            our_wrapper.unlink()
        removed, detail = True, "removed the statusLine entry (nothing was configured before)"
    elif command:
        target = _resolve_appendable_file(command, base)
        if target is not None and _remove_block(target):
            removed, detail = True, "removed our block from %s; the rest is untouched" % target

    hb = heartbeat_path(agent, base)
    if hb.exists():
        hb.unlink()
    return {"removed": removed, "detail": detail, "settings_path": path}


def is_installed(agent, base=None):
    if agent not in HOOK_AGENTS:
        return False
    _, key_path, _, _ = HOOK_AGENTS[agent]
    data = _read_json(settings_path(agent, base))
    current = _get_nested(data, key_path) or {}
    command = current.get("command") if isinstance(current, dict) else None
    if not command:
        return False
    if command == str(wrapper_path(agent, base)):
        return True
    target = _resolve_appendable_file(command, base)
    if target is None:
        return False
    try:
        return HOOK_BEGIN in target.read_text(encoding="utf-8")
    except OSError:
        return False


def is_active(agent, base=None, max_age=HEARTBEAT_MAX_AGE):
    """Installed AND actually being invoked right now by a live host session -- distinct
    from is_installed(), since a hook can be configured in an agent that isn't the one
    currently running. Freshness of the heartbeat file the hook touches on every call is
    what tells us a host is live, not just configured."""
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
