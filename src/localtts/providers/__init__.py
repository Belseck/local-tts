"""Provider registry."""

from localtts.errors import TTSError
from localtts.providers.base import Provider
from localtts.providers.command import CommandProvider
from localtts.providers.kokoro import KokoroProvider
from localtts.providers.llamacpp import LlamaCppProvider
from localtts.providers.openai import OpenAIProvider
from localtts.providers.piper import PiperProvider
from localtts.providers.rvc import RvcProvider

REGISTRY = {
    LlamaCppProvider.name: LlamaCppProvider,
    OpenAIProvider.name: OpenAIProvider,
    PiperProvider.name: PiperProvider,
    KokoroProvider.name: KokoroProvider,
    RvcProvider.name: RvcProvider,
    CommandProvider.name: CommandProvider,
}

DESCRIPTIONS = {
    "llamacpp": "local llama.cpp `llama-tts` binary (default)",
    "openai": "any OpenAI-compatible /v1/audio/speech endpoint",
    "piper": "local piper ONNX voices",
    "kokoro": "local Kokoro-82M via the kokoro-tts CLI, ~40 languages",
    "rvc": "voice conversion over another provider's output (rvc-python; not installed automatically)",
    "command": "custom shell template, e.g. espeak-ng or `say`",
}


def names():
    return list(REGISTRY)


def build(name, cfg, verbose=False):
    if name not in REGISTRY:
        raise TTSError("unknown provider %r (available: %s)" % (name, ", ".join(names())))
    settings = dict(cfg.get("providers", {}).get(name, {}))
    return REGISTRY[name](settings, verbose=verbose, cfg=cfg)


__all__ = ["REGISTRY", "DESCRIPTIONS", "Provider", "build", "names"]
