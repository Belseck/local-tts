"""Kokoro backend: small, fast, open-weight neural TTS (Kokoro-82M).

Targets a minimal `kokoro-tts` CLI wrapper -- `-o/--output`, `-v/--voice`, `-l/--lang`,
`-s/--speed`, text as trailing positional argument(s) -- the shape a simple wrapper
around the `kokoro`/`kokoro-onnx` Python package naturally takes, and the one
`local-tts-configure` sets up. If your own `kokoro-tts` differs, `extra_args` and
`binary` still let you point this provider at it.
"""

import os

from localtts.errors import TTSError
from localtts.providers.base import Provider

#: Only checked when kokoro.model_dir is set. Some kokoro CLIs (nazdridoy/kokoro-tts)
#: resolve these two files relative to their own working directory rather than a flag;
#: a wrapper around the kokoro/kokoro-onnx package directly usually manages its own
#: model path internally and needs neither this setting nor these files checked here.
MODEL_FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")


class KokoroProvider(Provider):
    name = "kokoro"
    default_format = "wav"

    def _model_dir(self):
        model_dir = os.path.expanduser(self.settings.get("model_dir") or "")
        if not model_dir:
            return None
        missing = [f for f in MODEL_FILES if not os.path.exists(os.path.join(model_dir, f))]
        if missing:
            raise TTSError("kokoro.model_dir is missing %s (looked in %s)"
                           % (", ".join(missing), model_dir))
        return model_dir

    def build_command(self, text, out_path, voice=None):
        exe = self.resolve_binary("binary", "kokoro-tts")
        cmd = [exe, "-o", out_path]

        chosen_voice = voice or self.settings.get("voice")
        if chosen_voice:
            cmd += ["-v", chosen_voice]
        lang = self.settings.get("lang")
        if lang:
            cmd += ["-l", lang]
        speed = self.settings.get("speed")
        if speed and float(speed) != 1.0:
            cmd += ["-s", str(speed)]
        cmd += list(self.settings.get("extra_args") or [])
        cmd.append(text)   # positional, last -- matches the CLI's `nargs="*"` text arg
        return cmd

    def synthesize(self, text, out_path, voice=None):
        model_dir = self._model_dir()
        cmd = self.build_command(text, out_path, voice)
        self.run(cmd, cwd=model_dir)
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise TTSError("kokoro-tts wrote no audio to %s" % out_path)
        return out_path

    def check(self):
        try:
            exe = self.resolve_binary("binary", "kokoro-tts")
        except TTSError as exc:
            return False, str(exc)
        try:
            model_dir = self._model_dir()
        except TTSError as exc:
            return False, "%s (%s)" % (exe, exc)
        return True, "%s%s" % (exe, " -> %s" % model_dir if model_dir else "")
