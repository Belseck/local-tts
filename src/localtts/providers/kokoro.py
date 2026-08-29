"""Kokoro backend: small, fast, open-weight neural TTS (Kokoro-82M).

Targets a minimal `kokoro-tts` CLI wrapper -- `-o/--output`, `-v/--voice`, `-l/--lang`,
`-s/--speed`, text as trailing positional argument(s) -- the shape a simple wrapper
around the `kokoro`/`kokoro-onnx` Python package naturally takes, and the one
`local-tts-configure` sets up. If your own `kokoro-tts` differs, `extra_args` and
`binary` still let you point this provider at it.
"""

import os
import tempfile

from localtts import audio as audiomod
from localtts import text as textutil
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
    #: -s/speed is a real flag (both the subprocess CLI and the server's payload) -- see
    #: kokoro_onnx.Kokoro.create()'s own signature, verified this session. No volume or
    #: pitch knob exists anywhere in kokoro/kokoro_onnx, so a <tag>'s volume multiplier is
    #: simply not realized here (honest silence, not a fabricated flag).
    supports_tone_tags = True

    def _model_dir(self):
        model_dir = os.path.expanduser(self.settings.get("model_dir") or "")
        if not model_dir:
            return None
        missing = [f for f in MODEL_FILES if not os.path.exists(os.path.join(model_dir, f))]
        if missing:
            raise TTSError("kokoro.model_dir is missing %s (looked in %s)"
                           % (", ".join(missing), model_dir))
        return model_dir

    def _effective_speed(self, profile):
        """Base `speed` setting times a <tag> profile's own speed multiplier, or just the
        base setting for a plain/neutral segment -- None if that comes out to 1.0 (kokoro's
        own default, flag omitted, exactly today's behavior)."""
        speed = float(self.settings.get("speed") or 1.0) * (profile["speed"] if profile else 1.0)
        return speed if speed != 1.0 else None

    def build_command(self, text, out_path, voice=None, speed=None):
        """`speed`, if given, overrides the configured `speed` setting for this one call
        (a <tag>-adjusted segment); leave it out for the plain, unadjusted case (also what
        --dry-run calls with)."""
        exe = self.resolve_binary("binary", "kokoro-tts")
        cmd = [exe, "-o", out_path]

        chosen_voice = voice or self.settings.get("voice")
        if chosen_voice:
            cmd += ["-v", chosen_voice]
        lang = self.settings.get("lang")
        if lang:
            cmd += ["-l", lang]
        effective_speed = speed if speed is not None else self.settings.get("speed")
        if effective_speed and float(effective_speed) != 1.0:
            cmd += ["-s", str(effective_speed)]
        cmd += list(self.settings.get("extra_args") or [])
        cmd.append(text)   # positional, last -- matches the CLI's `nargs="*"` text arg
        return cmd

    def synthesize(self, text, out_path, voice=None):
        segments = textutil.resolve_tone_segments(text, auto_tone=bool(self.settings.get("auto_tone")))
        if len(segments) == 1 and segments[0][1] is None:
            self._run_one(segments[0][0], out_path, voice, None)
            return out_path

        work = tempfile.mkdtemp(prefix="local-tts-tone-")
        parts = [os.path.join(work, "%04d.wav" % index) for index in range(len(segments))]
        try:
            for (chunk, profile), part in zip(segments, parts):
                self._run_one(chunk, part, voice, self._effective_speed(profile))
            audiomod.concat_wavs(parts, out_path)
        finally:
            for part in parts:
                if os.path.exists(part):
                    os.unlink(part)
            if os.path.isdir(work):
                os.rmdir(work)
        return out_path

    def _run_one(self, text, out_path, voice, speed):
        server_url = self.settings.get("server_url")
        if server_url:
            return self._synthesize_via_server(server_url, text, out_path, voice, speed)

        model_dir = self._model_dir()
        cmd = self.build_command(text, out_path, voice, speed=speed)
        self.run(cmd, cwd=model_dir)
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise TTSError("kokoro-tts wrote no audio to %s" % out_path)
        return out_path

    def _synthesize_via_server(self, server_url, text, out_path, voice, speed=None):
        """Talk to a persistent server that already has the model loaded, instead of
        spawning kokoro-tts fresh (and reloading its model) for this one call. voice/lang
        travel per request, same as the subprocess path -- the server holding the model
        resident doesn't fix them to whatever it started with."""
        self.ensure_server(server_url, self.settings.get("server_start"),
                           float(self.settings.get("server_timeout") or 30))
        payload = {"text": text}
        chosen_voice = voice or self.settings.get("voice")
        if chosen_voice:
            payload["voice"] = chosen_voice
        lang = self.settings.get("lang")
        if lang:
            payload["lang"] = lang
        effective_speed = speed if speed is not None else self.settings.get("speed")
        if effective_speed and float(effective_speed) != 1.0:
            payload["speed"] = float(effective_speed)
        audio_bytes = self.post_for_audio(server_url, "/synthesize", payload)
        with open(out_path, "wb") as fh:
            fh.write(audio_bytes)
        return out_path

    def check(self):
        server_url = self.settings.get("server_url")
        if server_url:
            # A quick probe only -- tts check must not have the side effect of starting
            # a server, that's what actually speaking (or a deliberate warmup) does.
            if self.server_alive(server_url):
                return True, "server at %s (already running)" % server_url
            if self.settings.get("server_start"):
                return True, "server at %s (not running yet -- starts automatically on first use)" % server_url
            return False, "server at %s is not running, and no server_start is configured" % server_url

        try:
            exe = self.resolve_binary("binary", "kokoro-tts")
        except TTSError as exc:
            return False, str(exc)
        try:
            model_dir = self._model_dir()
        except TTSError as exc:
            return False, "%s (%s)" % (exe, exc)
        return True, "%s%s" % (exe, " -> %s" % model_dir if model_dir else "")
