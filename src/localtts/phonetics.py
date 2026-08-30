"""User scripts that shape the `/IPA/` table before it is sent to a backend.

The pronunciation dictionary answers "how is this word said" for words the user has
written down by hand. That is the right place for a handful of names and borrowed terms,
and the wrong place for anything generated: a lexicon, a company's glossary, a script
that transcribes a word from its spelling. Those want to run at synthesis time, and they
want to be somebody else's code -- this package has no runtime dependencies and cannot
grow a transcriber of its own.

So a hook is an executable named in `phonetics_hooks`. It gets the resolved table for
this utterance on stdin as JSON, and prints the table it wants used:

    stdin   {"text": "ya subí el pull request", "lang": "es", "provider": "kokoro",
             "phonetics": {"pull request": "pˈʊl ɹᵻkwˈɛst"}}
    stdout  {"phonetics": {"pull request": "pˈʊl ɹᵻkwˈɛst", "croissant": "kʁwasɑ̃"}}

Hooks run in the order they are listed, each one seeing what the last returned, and they
can add, rewrite or drop entries. They cannot touch the text: a hook that misbehaves must
never change *what* is said, only how a word in it is transcribed -- and the words it did
not name come out exactly as they would have with no hook at all.

Everything that can go wrong (a non-zero exit, unparseable output, a script that hangs)
leaves the table exactly as the previous hook left it, with one line on stderr. Speech
that is slightly wrong beats speech that does not happen.
"""

import json
import os
import subprocess
import sys

from localtts import text as textutil

#: Hooks that already failed this process, so a broken one is reported once rather than
#: per fragment -- an utterance is several calls and the same complaint eight times is
#: noise that hides the first one.
_REPORTED = set()


def _warn(script, message):
    if (script, message) in _REPORTED:
        return
    _REPORTED.add((script, message))
    print("phonetics hook %s: %s" % (script, message), file=sys.stderr, flush=True)


def _normalize(table):
    """Accept what a hook is likely to print, not only the exact shape we send.

    `/kʁwasɑ̃/` and `kʁwasɑ̃` both mean the same thing to whoever wrote the script, and
    keys are matched case-insensitively downstream, so the same normalization the
    dictionary's own resolver applies is applied here rather than later.
    """
    clean = {}
    for key, value in (table or {}).items():
        word = str(key).strip().lower()
        ipa = str(value or "").strip()
        if ipa.startswith("/") and ipa.endswith("/") and len(ipa) > 1:
            ipa = textutil.phonetic_text(ipa) or ipa[1:-1].strip()
        if word and ipa:
            clean[word] = ipa
    return clean


def _command(script):
    """How to run this hook: itself, or through this interpreter if it is a .py.

    A `.py` without the executable bit is the overwhelmingly common way to get this
    wrong, and "permission denied" for a file the user just wrote is a worse answer than
    simply running it.
    """
    path = os.path.expanduser(script)
    if os.access(path, os.X_OK):
        return [path]
    if path.endswith(".py"):
        return [sys.executable, path]
    return [path]


def run_hooks(table, text, lang, provider, cfg=None, timeout=None):
    """The table after every configured hook has had a turn at it."""
    cfg = cfg or {}
    scripts = cfg.get("phonetics_hooks") or []
    if not scripts:
        return table
    if timeout is None:
        try:
            timeout = float(cfg.get("phonetics_hook_timeout") or 5)
        except (TypeError, ValueError):
            timeout = 5.0

    current = dict(table or {})
    for script in scripts:
        payload = json.dumps({"text": text, "lang": lang or "", "provider": provider,
                              "phonetics": current}, ensure_ascii=False)
        try:
            done = subprocess.run(_command(script), input=payload.encode("utf-8"),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=timeout)
        except subprocess.TimeoutExpired:
            _warn(script, "took longer than %gs, so this call goes on without it "
                          "(phonetics_hook_timeout)" % timeout)
            continue
        except OSError as exc:
            _warn(script, "could not be run (%s)" % exc)
            continue
        if done.returncode != 0:
            detail = (done.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            _warn(script, "exited %d%s" % (done.returncode,
                                           " -- %s" % detail[-1] if detail else ""))
            continue
        try:
            answer = json.loads(done.stdout.decode("utf-8", "replace") or "{}")
        except ValueError:
            _warn(script, "printed something that is not JSON, so it was ignored")
            continue
        if not isinstance(answer, dict) or not isinstance(answer.get("phonetics", {}), dict):
            _warn(script, 'printed no "phonetics" object, so it was ignored')
            continue
        current = _normalize(answer.get("phonetics", current))
    return current
