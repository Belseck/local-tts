"""OpenAI-compatible /v1/audio/speech backend (stdlib urllib, no SDK)."""

import json
import os
import tempfile
import urllib.error
import urllib.request

from localtts import audio as audiomod
from localtts import text as textutil
from localtts.errors import TTSError
from localtts.providers.base import Provider

FORMATS = ("wav", "mp3", "opus", "aac", "flac", "pcm")

#: Only these accept the "instructions" (tone/style) field -- verified against OpenAI's
#: own API reference, 2026-08. tts-1 and tts-1-hd reject it.
INSTRUCTIONS_MODELS = ("gpt-4o-mini-tts", "gpt-4o-mini-tts-2025-12-15")


class OpenAIProvider(Provider):
    name = "openai"
    default_format = "mp3"
    supports_tone_tags = True

    def api_key(self):
        return self.settings.get("api_key") or os.environ.get("OPENAI_API_KEY", "")

    def resolved_segments(self, text):
        """(chunk, profile) pairs -- see text.resolve_tone_segments(). A segment with no
        active <tag>/auto_tone falls back to the flat `tone` setting (instructions only,
        no speed change) if any, so a plain global override still applies with zero
        <tag>s anywhere. Public (not `_segments`) because --dry-run calls it to show what
        synthesize() would actually send, same reasoning as
        RvcProvider.base_provider_instance()."""
        flat_tone = self.settings.get("tone") or None
        segments = textutil.resolve_tone_segments(text, auto_tone=bool(self.settings.get("auto_tone")))
        resolved = []
        for chunk, profile in segments:
            if profile is None and flat_tone:
                profile = {"instructions": flat_tone, "speed": 1.0, "volume": 1.0}
            resolved.append((chunk, profile))
        return resolved

    def _request(self, text, voice, response_format, profile):
        base = (self.settings.get("base_url") or "").rstrip("/")
        if not base:
            raise TTSError("openai.base_url is not set")
        model = self.settings.get("model") or "tts-1"
        instructions = profile["instructions"] if profile else None
        if instructions and model not in INSTRUCTIONS_MODELS:
            raise TTSError(
                "openai.tone (or a <tag>) needs a model that accepts voice "
                "instructions -- %s does not. Set `tts config --set openai.model=%s`."
                % (model, INSTRUCTIONS_MODELS[0])
            )

        payload = {
            "model": model,
            "input": text,
            "voice": voice or self.settings.get("voice") or "alloy",
            "response_format": response_format,
        }
        if instructions:
            payload["instructions"] = instructions
        speed = float(self.settings.get("speed") or 1.0) * (profile["speed"] if profile else 1.0)
        if speed != 1.0:
            payload["speed"] = min(4.0, max(0.25, speed))   # the API's own documented bounds

        headers = {"Content-Type": "application/json"}
        key = self.api_key()
        if key:
            headers["Authorization"] = "Bearer %s" % key

        request = urllib.request.Request(
            base + "/audio/speech",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        timeout = float(self.settings.get("timeout") or 120)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace").strip()
            raise TTSError("HTTP %s from %s: %s" % (exc.code, base, detail[:500]))
        except urllib.error.URLError as exc:
            raise TTSError("could not reach %s: %s" % (base, exc.reason))
        if not body:
            raise TTSError("server returned an empty audio body")
        return body

    def synthesize(self, text, out_path, voice=None):
        fmt = os.path.splitext(out_path)[1].lstrip(".").lower() or self.default_format
        if fmt not in FORMATS:
            raise TTSError("unsupported output format %r (choose from %s)" % (fmt, ", ".join(FORMATS)))

        segments = self.resolved_segments(text)
        if len(segments) == 1:
            chunk, profile = segments[0]
            audio = self._request(chunk, voice, fmt, profile)
            with open(out_path, "wb") as fh:
                fh.write(audio)
            return out_path

        # A mid-text tone change means separate calls with a different instructions/speed
        # each, joined afterward -- and joining losslessly needs a real PCM container, so
        # each segment is fetched as wav regardless of `fmt`. concat_wavs() (via wave.open)
        # can only make sense of that, not of mp3/opus/etc. The bytes actually written to
        # out_path are therefore wav even when its own extension says otherwise; every
        # player this tool uses (audio.PLAYERS) sniffs the real header, not the name, so
        # playback is unaffected -- only an explicit `--output foo.mp3` combined with
        # <tag>s would save a file whose *name* no longer matches its contents.
        work = tempfile.mkdtemp(prefix="local-tts-tone-")
        parts = [os.path.join(work, "%04d.wav" % index) for index in range(len(segments))]
        joined = os.path.join(work, "joined.wav")
        try:
            for (chunk, profile), part in zip(segments, parts):
                audio_bytes = self._request(chunk, voice, "wav", profile)
                with open(part, "wb") as fh:
                    fh.write(audio_bytes)
            audiomod.concat_wavs(parts, joined)
            # A plain copy, not os.replace(): `work` is in the system temp dir, which may
            # be a different filesystem than out_path (a cross-device rename would raise).
            with open(joined, "rb") as fh:
                data = fh.read()
            with open(out_path, "wb") as fh:
                fh.write(data)
        finally:
            for part in parts:
                if os.path.exists(part):
                    os.unlink(part)
            if os.path.exists(joined):
                os.unlink(joined)
            if os.path.isdir(work):
                os.rmdir(work)
        return out_path

    def check(self):
        base = self.settings.get("base_url") or ""
        if not base:
            return False, "base_url is not set"
        remote = base.startswith("https://api.openai.com")
        if remote and not self.api_key():
            return False, "%s (no api_key and $OPENAI_API_KEY is unset)" % base
        return True, "%s (model=%s)" % (base, self.settings.get("model"))
