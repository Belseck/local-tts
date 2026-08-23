"""OpenAI-compatible /v1/audio/speech backend (stdlib urllib, no SDK)."""

import json
import os
import urllib.error
import urllib.request

from localtts.errors import TTSError
from localtts.providers.base import Provider

FORMATS = ("wav", "mp3", "opus", "aac", "flac", "pcm")


class OpenAIProvider(Provider):
    name = "openai"
    default_format = "mp3"

    def api_key(self):
        return self.settings.get("api_key") or os.environ.get("OPENAI_API_KEY", "")

    def synthesize(self, text, out_path, voice=None):
        base = (self.settings.get("base_url") or "").rstrip("/")
        if not base:
            raise TTSError("openai.base_url is not set")

        fmt = os.path.splitext(out_path)[1].lstrip(".").lower() or self.default_format
        if fmt not in FORMATS:
            raise TTSError("unsupported output format %r (choose from %s)" % (fmt, ", ".join(FORMATS)))

        payload = {
            "model": self.settings.get("model") or "tts-1",
            "input": text,
            "voice": voice or self.settings.get("voice") or "alloy",
            "response_format": fmt,
        }
        speed = self.settings.get("speed")
        if speed and float(speed) != 1.0:
            payload["speed"] = float(speed)

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
                audio = response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace").strip()
            raise TTSError("HTTP %s from %s: %s" % (exc.code, base, body[:500]))
        except urllib.error.URLError as exc:
            raise TTSError("could not reach %s: %s" % (base, exc.reason))

        if not audio:
            raise TTSError("server returned an empty audio body")
        with open(out_path, "wb") as fh:
            fh.write(audio)
        return out_path

    def check(self):
        base = self.settings.get("base_url") or ""
        if not base:
            return False, "base_url is not set"
        remote = base.startswith("https://api.openai.com")
        if remote and not self.api_key():
            return False, "%s (no api_key and $OPENAI_API_KEY is unset)" % base
        return True, "%s (model=%s)" % (base, self.settings.get("model"))
