"""Provider registry."""

from localtts.errors import TTSError
from localtts.providers.base import Provider
from localtts.providers.command import CommandProvider
from localtts.providers.llamacpp import LlamaCppProvider
from localtts.providers.openai import OpenAIProvider
from localtts.providers.piper import PiperProvider

REGISTRY = {
    LlamaCppProvider.name: LlamaCppProvider,
    OpenAIProvider.name: OpenAIProvider,
    PiperProvider.name: PiperProvider,
    CommandProvider.name: CommandProvider,
}

DESCRIPTIONS = {
    "llamacpp": "local llama.cpp `llama-tts` binary (default)",
    "openai": "any OpenAI-compatible /v1/audio/speech endpoint",
    "piper": "local piper ONNX voices",
    "command": "custom shell template, e.g. espeak-ng or `say`",
}


def names():
    return list(REGISTRY)


def build(name, cfg, verbose=False):
    if name not in REGISTRY:
        raise TTSError("unknown provider %r (available: %s)" % (name, ", ".join(names())))
    settings = dict(cfg.get("providers", {}).get(name, {}))
    return REGISTRY[name](settings, verbose=verbose)


__all__ = ["REGISTRY", "DESCRIPTIONS", "Provider", "build", "names"]
