"""Piper backend: fast, tiny, fully offline ONNX voices."""

import os

from localtts.errors import TTSError
from localtts.providers.base import Provider


class PiperProvider(Provider):
    name = "piper"
    default_format = "wav"

    def synthesize(self, text, out_path, voice=None):
        exe = self.resolve_binary("binary", "piper")
        model = os.path.expanduser(voice or self.settings.get("model") or "")
        if not model:
            raise TTSError(
                "piper needs a voice model: `tts config --set piper.model=/path/to/voice.onnx` "
                "(download voices from https://huggingface.co/rhasspy/piper-voices)"
            )
        if not os.path.exists(model):
            raise TTSError("piper voice not found: %s" % model)

        cmd = [exe, "--model", model, "--output_file", out_path]
        speaker = self.settings.get("speaker")
        if speaker is not None:
            cmd += ["--speaker", str(speaker)]
        cmd += list(self.settings.get("extra_args") or [])

        self.run(cmd, stdin_text=text + "\n")
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise TTSError("piper wrote no audio to %s" % out_path)
        return out_path

    def check(self):
        try:
            exe = self.resolve_binary("binary", "piper")
        except TTSError as exc:
            return False, str(exc)
        model = os.path.expanduser(self.settings.get("model") or "")
        if not model:
            return False, "%s (no voice model configured)" % exe
        if not os.path.exists(model):
            return False, "%s (voice missing: %s)" % (exe, model)
        return True, "%s -> %s" % (exe, model)
