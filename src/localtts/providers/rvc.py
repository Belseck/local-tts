"""RVC backend: retrieval-based voice conversion over another provider's output.

rvc-python (https://github.com/daswer123/rvc-python) does audio-to-audio conversion
only -- confirmed against its own README, it has no text input at all. So this provider
composes another one: synthesize with a normal TTS backend first, then run the result
through rvc-python to convert it to the target voice.

Not installed automatically, and never will be by this file -- it pulls in torch and is
sizable. Config points at wherever the user (or their agent, after asking) installed it
into its own venv, the same pattern piper and kokoro use for keeping heavy dependencies
out of local-tts's own install.
"""

import os
import tempfile

from localtts.errors import TTSError
from localtts.providers.base import Provider


class RvcProvider(Provider):
    name = "rvc"
    default_format = "wav"

    def _python(self):
        python = os.path.expanduser(self.settings.get("python") or "")
        if not python:
            raise TTSError(
                "rvc needs the python interpreter from the venv rvc-python is installed "
                "in: `tts config --set rvc.python=/path/to/rvc-venv/bin/python` -- "
                "rvc-python is not installed automatically, see the local-tts-configure skill"
            )
        if not os.path.exists(python):
            raise TTSError("rvc.python not found: %s" % python)
        return python

    def _model(self):
        model = os.path.expanduser(self.settings.get("model") or "")
        if not model:
            raise TTSError("rvc needs a voice model: `tts config --set rvc.model=/path/to/model.pth`")
        if not os.path.exists(model):
            raise TTSError("rvc voice model not found: %s" % model)
        return model

    def _base_name(self):
        return self.settings.get("base_provider") or (self.cfg or {}).get("provider") or "piper"

    def base_provider_instance(self):
        base_name = self._base_name()
        if base_name == self.name:
            raise TTSError("rvc.base_provider cannot be rvc itself")
        if self.cfg is None:
            raise TTSError(
                "rvc was built without the full configuration, so it cannot construct its "
                "base provider (internal error -- report this)"
            )
        from localtts import providers as providers_module   # local: avoids a cycle at import time
        return providers_module.build(base_name, self.cfg, verbose=self.verbose)

    def build_command(self, wav_in, out_path):
        """The voice-conversion half only. There is no single command for the whole
        pipeline -- the base synthesis step is a separate provider, possibly chunked --
        so --dry-run shows only this half, with a placeholder input path."""
        python = self._python()
        model = self._model()
        cmd = [python, "-m", "rvc_python", "cli", "-i", wav_in, "-o", out_path, "-mp", model]

        index = os.path.expanduser(self.settings.get("index") or "")
        if index:
            cmd += ["-ip", index]
        device = self.settings.get("device")
        if device:
            cmd += ["-de", str(device)]
        method = self.settings.get("method")
        if method:
            cmd += ["-me", method]
        pitch = self.settings.get("pitch")
        if pitch:
            cmd += ["-pi", str(pitch)]
        index_rate = self.settings.get("index_rate")
        if index_rate is not None:
            cmd += ["-ir", str(index_rate)]
        protect = self.settings.get("protect")
        if protect is not None:
            cmd += ["-pr", str(protect)]
        return cmd + list(self.settings.get("extra_args") or [])

    def synthesize(self, text, out_path, voice=None):
        from localtts import text as textutil   # local: text.py doesn't import providers,
                                                 # kept local anyway for symmetry with above
        base = self.base_provider_instance()
        # Always .wav regardless of the base provider's own default_format (e.g. openai
        # defaults to mp3) -- rvc-python needs a real wav to read, and every provider
        # here picks its output format from the path's extension.
        handle, base_wav = tempfile.mkstemp(prefix="local-tts-rvc-base-", suffix=".wav")
        os.close(handle)
        try:
            textutil.synthesize_chunked(base, text, base_wav, voice=voice)
            server_url = self.settings.get("server_url")
            if server_url:
                self._convert_via_server(server_url, base_wav, out_path)
            else:
                cmd = self.build_command(base_wav, out_path)
                self.run(cmd)
        finally:
            if os.path.exists(base_wav):
                os.unlink(base_wav)

        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise TTSError("rvc-python wrote no audio to %s" % out_path)
        return out_path

    def _convert_via_server(self, server_url, wav_in, out_path):
        """Talk to a persistent server that already has the model (and torch) loaded,
        instead of paying that cost on every call. The model/index is fixed at server
        startup -- rvc.model/rvc.index configure the CLI fallback above, not a running
        server; restart the server with different startup args to change its voice."""
        self.ensure_server(server_url, self.settings.get("server_start"),
                           float(self.settings.get("server_timeout") or 60))
        payload = {"input_path": os.path.abspath(wav_in)}
        pitch = self.settings.get("pitch")
        if pitch:
            payload["pitch"] = pitch
        device = self.settings.get("device")
        if device:
            payload["device"] = str(device)
        audio = self.post_for_audio(server_url, "/convert", payload)
        with open(out_path, "wb") as fh:
            fh.write(audio)

    def check(self):
        server_url = self.settings.get("server_url")
        if server_url:
            # A quick probe only -- tts check must not have the side effect of starting
            # a server (a torch model load is exactly the cost this mode exists to avoid
            # paying casually).
            if self.server_alive(server_url):
                detail = "server at %s (already running)" % server_url
            elif self.settings.get("server_start"):
                detail = "server at %s (not running yet -- starts automatically on first use)" % server_url
            else:
                return False, "server at %s is not running, and no server_start is configured" % server_url
            return True, "%s, base voice from %s" % (detail, self._base_name())

        try:
            python = self._python()
        except TTSError as exc:
            return False, str(exc)
        try:
            model = self._model()
        except TTSError as exc:
            return False, "%s (%s)" % (python, exc)
        return True, "%s -> %s, base voice from %s" % (python, model, self._base_name())
