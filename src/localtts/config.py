"""Configuration loading: defaults < config file < environment < CLI flags."""

import json
import os
import sys
from copy import deepcopy
from pathlib import Path

from localtts.errors import TTSError

APP_NAME = "local-tts"
ENV_PREFIX = "LOCALTTS_"

DEFAULTS = {
    # Provider used when --provider is not given.
    "provider": "llamacpp",
    # Play the generated audio after synthesis (unless --output is given).
    "play": True,
    # Force a specific playback command (e.g. "ffplay"). Empty => autodetect.
    "player": "",
    # Which backend speaks which language, e.g.
    #   {"es": {"provider": "piper", "voice": "~/voices/es_MX-claude-high.onnx"}}
    # Shared by every coding agent so the preference is remembered in one place.
    "languages": {},
    "providers": {
        "llamacpp": {
            "binary": "llama-tts",
            # Path to the TTS gguf model. Leave empty to let llama.cpp fetch
            # its default OuteTTS weights (--tts-oute-default).
            "model": "",
            # Path to the vocoder gguf (WavTokenizer). Required with "model".
            "vocoder": "",
            # Alternative to local paths: pull from Hugging Face.
            "hf_repo": "",
            "hf_file": "",
            "hf_repo_vocoder": "",
            "hf_file_vocoder": "",
            # Speaker/voice profile JSON accepted by --tts-speaker-file.
            "speaker_file": "",
            # OuteTTS degrades past a couple of sentences per prompt, so long text is
            # synthesized in pieces and joined. 0 disables chunking.
            "max_words": 26,
            # Chunks run concurrently, up to this many at once. Each llama-tts call pays
            # several seconds of fixed process-startup/model-load cost regardless of how
            # short the chunk is, so overlapping chunks matters more than raising
            # max_words (which risks the degradation above). Measured on one RTX 4070
            # laptop GPU: 2 concurrent calls ~1.4x faster wall-clock than sequential;
            # 4 concurrent was not meaningfully faster than 2 (single-GPU compute is
            # still serialized) but used more VRAM for no benefit. 2 is a safe default
            # across GPU sizes; raise it if you have VRAM/cores to spare.
            "max_workers": 2,
            "threads": 0,          # 0 => let llama.cpp decide
            "gpu_layers": None,    # None => leave llama.cpp default
            "guide_tokens": True,  # improves word recall on OuteTTS
            "extra_args": [],
        },
        "openai": {
            # Works with api.openai.com and any OpenAI-compatible server
            # (openedai-speech, Kokoro-FastAPI, LocalAI, ...).
            "base_url": "https://api.openai.com/v1",
            "api_key": "",         # falls back to $OPENAI_API_KEY
            "model": "tts-1",
            "voice": "alloy",
            "speed": 1.0,
            "timeout": 120,
        },
        "piper": {
            "binary": "piper",
            "model": "",           # path to a .onnx voice
            "speaker": None,       # speaker id for multi-speaker voices
            "extra_args": [],
        },
        "command": {
            # Escape hatch: any CLI that writes a wav file.
            # {text} and {output} are substituted as single argv items.
            "template": "espeak-ng -w {output} {text}",
        },
    },
}


TOP_LEVEL_KEYS = ("provider", "play", "player")
LANGUAGE_KEYS = ("provider", "voice")


def config_root():
    """Per-platform config directory: %APPDATA% on Windows, ~/.config elsewhere.

    $XDG_CONFIG_HOME wins everywhere when it is set, so a user can keep one location
    across machines.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg)
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata)
    return Path.home() / ".config"


def config_dir():
    return config_root() / APP_NAME


def config_path():
    override = os.environ.get(ENV_PREFIX + "CONFIG")
    return Path(override).expanduser() if override else config_dir() / "config.json"


def _deep_merge(base, override):
    out = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _coerce(raw, current):
    """Turn a string from the environment or --set into the right type."""
    if isinstance(current, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(current, int) and not isinstance(current, bool):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    if isinstance(current, list):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return raw.split()
        return parsed if isinstance(parsed, list) else [parsed]
    return raw


def _apply_env(cfg):
    """LOCALTTS_PROVIDER, LOCALTTS_PLAY, LOCALTTS_<PROVIDER>_<KEY>."""
    for env_key, raw in os.environ.items():
        if not env_key.startswith(ENV_PREFIX):
            continue
        suffix = env_key[len(ENV_PREFIX):].lower()
        if suffix in TOP_LEVEL_KEYS:
            cfg[suffix] = _coerce(raw, DEFAULTS[suffix])
            continue
        if suffix.startswith("lang_"):
            code = suffix[len("lang_"):].replace("_", "-")
            if code:
                cfg.setdefault("languages", {})[code] = parse_language_value(raw)
            continue
        for provider, settings in cfg["providers"].items():
            prefix = provider + "_"
            if suffix.startswith(prefix):
                key = suffix[len(prefix):]
                if key in settings:
                    settings[key] = _coerce(raw, settings[key])
                break
    return cfg


def load():
    cfg = deepcopy(DEFAULTS)
    path = config_path()
    if path.exists():
        try:
            user = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise TTSError("invalid JSON in %s: %s" % (path, exc))
        if not isinstance(user, dict):
            raise TTSError("config in %s must be a JSON object" % path)
        cfg = _deep_merge(cfg, user)
    return _apply_env(cfg)


def normalize_language(code):
    """'es-MX', 'es_MX', 'ES' -> ('es-mx', 'es'): the exact tag and its base."""
    tag = str(code or "").strip().lower().replace("_", "-")
    return tag, tag.split("-")[0]


def language_entry(cfg, code):
    """The {provider, voice} recorded for a language, matching 'es-MX' before 'es'."""
    languages = cfg.get("languages") or {}
    exact, base = normalize_language(code)
    for key in (exact, base):
        for stored, entry in languages.items():
            if normalize_language(stored)[0] == key:
                return entry
    return None


def parse_language_value(raw):
    """Accept 'piper' or 'piper:/path/to/voice.onnx'."""
    provider, _, voice = str(raw).partition(":")
    provider = provider.strip()
    if provider and provider not in DEFAULTS["providers"]:
        raise TTSError(
            "unknown provider %r (available: %s)" % (provider, ", ".join(sorted(DEFAULTS["providers"])))
        )
    entry = {"provider": provider} if provider else {}
    if voice.strip():
        entry["voice"] = voice.strip()
    return entry


def read_user_config():
    """The on-disk config only, without defaults or environment applied."""
    path = config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise TTSError("invalid JSON in %s: %s" % (path, exc))


def set_values(assignments):
    """Persist "key=value" or "provider.key=value" pairs. Returns the new config."""
    user = read_user_config()
    for item in assignments:
        if "=" not in item:
            raise TTSError("expected key=value, got %r" % item)
        key, raw = item.split("=", 1)
        key = key.strip()
        if key.startswith("languages."):
            rest = key[len("languages."):]
            code, _, field = rest.partition(".")
            if not code:
                raise TTSError("expected languages.<code>=<provider>[:<voice>]")
            bucket = user.setdefault("languages", {}).setdefault(code, {})
            if field:
                if field not in LANGUAGE_KEYS:
                    raise TTSError(
                        "unknown language key %r (valid: %s)" % (field, ", ".join(LANGUAGE_KEYS))
                    )
                if field == "provider" and raw and raw not in DEFAULTS["providers"]:
                    raise TTSError("unknown provider %r" % raw)
                bucket[field] = raw
            else:
                bucket.clear()
                bucket.update(parse_language_value(raw))
            if not bucket:
                user["languages"].pop(code, None)
            continue
        if "." in key:
            provider, sub = key.split(".", 1)
            if provider not in DEFAULTS["providers"]:
                raise TTSError("unknown provider %r" % provider)
            known = DEFAULTS["providers"][provider]
            if sub not in known:
                raise TTSError(
                    "unknown key %r for provider %r (valid: %s)"
                    % (sub, provider, ", ".join(sorted(known)))
                )
            user.setdefault("providers", {}).setdefault(provider, {})[sub] = _coerce(raw, known[sub])
        else:
            if key not in TOP_LEVEL_KEYS:
                raise TTSError(
                    "unknown key %r (use %s, or <provider>.<key>)"
                    % (key, ", ".join(TOP_LEVEL_KEYS))
                )
            user[key] = _coerce(raw, DEFAULTS[key])

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(user, indent=2) + "\n", encoding="utf-8")
    return user


coerce = _coerce  # public alias for the CLI
