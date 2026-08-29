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
    #: rvc splits tone segments itself -- see synthesize(). Converting one merged wav
    #: would give every segment the same flat delivery, which is the whole thing tags
    #: exist to avoid.
    handles_tone_segments = True

    #: rvc does honor <tag>s, just not by passing them to anything: synthesize() splits
    #: on them and shapes each converted span. Declaring this keeps synthesize_chunked()
    #: from stripping the markup before rvc ever gets to see where the spans are.
    supports_tone_tags = True
    #: Neither dimension is realized during synthesis. resolve_tone_segments() hands back
    #: tag-free text, so the base provider is given no markup to act on even when it
    #: could (piper's --length-scale) -- the profile is applied to the converted audio
    #: instead. Stated explicitly because assuming the base did it would silently drop
    #: the emotion.
    realizes_speed = False
    realizes_volume = False

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

    def server_model_name(self):
        """Which resident voice to ask a multi-model server for, or "" for whatever it
        loaded first. Language-specific mapping wins over the flat default, and an exact
        tag beats its base language (es-MX before es), matching how the language memory
        itself resolves. Public because `tts check` and --dry-run both report it."""
        by_lang = self.settings.get("language_models") or {}
        tag = (self.lang or "").strip()
        if tag and by_lang:
            for candidate in (tag, tag.replace("_", "-"), tag.split("-")[0].split("_")[0]):
                if candidate in by_lang:
                    return by_lang[candidate]
        return self.settings.get("server_model") or ""

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
        return providers_module.build(base_name, self.cfg, verbose=self.verbose, lang=self.lang)

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
        from localtts import audio as audiomod
        from localtts import text as textutil   # local: text.py doesn't import providers,
                                                 # kept local anyway for symmetry with above
        base = self.base_provider_instance()
        segments = textutil.resolve_tone_segments(
            text, auto_tone=bool(base.settings.get("auto_tone")) if hasattr(base, "settings") else False)
        if len(segments) == 1 and segments[0][1] is None:
            self._synthesize_one(base, text, out_path, voice, profile=None)
            return out_path

        # Each tagged span makes its own trip through the converter: rvc works on audio,
        # so a span's delivery has to already be in the wav it receives.
        work = tempfile.mkdtemp(prefix="local-tts-rvc-tone-")
        parts = [os.path.join(work, "%04d.wav" % index) for index in range(len(segments))]
        try:
            for (chunk, profile), part in zip(segments, parts):
                self._synthesize_one(base, chunk, part, voice, profile)
            audiomod.concat_wavs(parts, out_path)
        finally:
            for part in parts:
                if os.path.exists(part):
                    os.unlink(part)
            if os.path.isdir(work):
                os.rmdir(work)
        return out_path

    def _synthesize_one(self, base, chunk, out_path, voice, profile):
        """Base synthesis -> conversion -> whatever tone the base could not realize.

        resolve_tone_segments() strips the markup, so the base is handed plain text and
        realizes nothing itself -- the profile is applied here, once, to the converted
        span. Applied *after* conversion rather than before so the shaping survives:
        rvc resynthesizes its output and would otherwise flatten it back out.
        """
        from localtts import audiofx
        from localtts import text as textutil

        handle, base_wav = tempfile.mkstemp(prefix="local-tts-rvc-base-", suffix=".wav")
        os.close(handle)
        try:
            textutil.synthesize_chunked(base, chunk, base_wav, voice=voice)
            server_url = self.settings.get("server_url")
            if server_url:
                self._convert_via_server(server_url, base_wav, out_path)
            else:
                self.run(self.build_command(base_wav, out_path))
        finally:
            if os.path.exists(base_wav):
                os.unlink(base_wav)

        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise TTSError("rvc-python wrote no audio to %s" % out_path)

        if profile:
            audiofx.apply_profile(
                out_path,
                speed=1.0 if self.realizes_speed else profile["speed"],
                volume=1.0 if self.realizes_volume else profile["volume"],
            )
        return out_path


    def _convert_via_server(self, server_url, wav_in, out_path):
        """Talk to a persistent server that already has the model (and torch) loaded,
        instead of paying that cost on every call. Which voices exist is fixed at server
        startup -- rvc.model/rvc.index configure the CLI fallback above, not a running
        server -- but a server started with several `--model name=path` pairs keeps them
        all resident, and `rvc.language_models`/`rvc.server_model` pick one per request.
        Adding a voice still means restarting the server with an extra pair."""
        self.ensure_server(server_url, self.settings.get("server_start"),
                           float(self.settings.get("server_timeout") or 60))
        payload = {"input_path": os.path.abspath(wav_in)}
        model_name = self.server_model_name()
        if model_name:
            # A single-model server (or an older one) simply ignores this key, so
            # naming a voice is always safe to send.
            payload["model"] = model_name
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
            voices = self.settings.get("server_models") or {}
            by_lang = self.settings.get("language_models") or {}
            if voices:
                detail += ", %d voice%s resident (%s)" % (
                    len(voices), "" if len(voices) == 1 else "s", ", ".join(sorted(voices)))
            if by_lang:
                detail += "; " + ", ".join("%s->%s" % (k, by_lang[k]) for k in sorted(by_lang))
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
