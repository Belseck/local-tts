"""Stdlib-only tests: python -m unittest discover -s tests"""

import json
import os
import signal
import sys
import tempfile
import threading
import time
import types
import unittest
import wave
from pathlib import Path

from localtts import audio, config, hooks, providers, skills, text as textutil
from localtts.cli import _resolve_session, _synthesize, main
from localtts.errors import TTSError
from localtts.providers.base import Provider
from localtts.providers.command import CommandProvider
from localtts.providers.kokoro import KokoroProvider
from localtts.providers.llamacpp import LlamaCppProvider
from localtts.providers.rvc import RvcProvider


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"
        os.environ["LOCALTTS_CONFIG"] = str(self.path)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.environ.pop, "LOCALTTS_CONFIG", None)

    def test_defaults_when_no_file(self):
        cfg = config.load()
        self.assertEqual(cfg["provider"], "llamacpp")
        self.assertTrue(cfg["play"])

    def test_file_overrides_defaults_without_dropping_siblings(self):
        self.path.write_text(json.dumps({"providers": {"llamacpp": {"threads": 8}}}))
        cfg = config.load()
        self.assertEqual(cfg["providers"]["llamacpp"]["threads"], 8)
        self.assertEqual(cfg["providers"]["llamacpp"]["binary"], "llama-tts")

    def test_env_beats_file(self):
        self.path.write_text(json.dumps({"provider": "piper"}))
        os.environ["LOCALTTS_PROVIDER"] = "openai"
        self.addCleanup(os.environ.pop, "LOCALTTS_PROVIDER", None)
        self.assertEqual(config.load()["provider"], "openai")

    def test_env_coerces_types(self):
        os.environ["LOCALTTS_LLAMACPP_THREADS"] = "12"
        os.environ["LOCALTTS_PLAY"] = "false"
        self.addCleanup(os.environ.pop, "LOCALTTS_LLAMACPP_THREADS", None)
        self.addCleanup(os.environ.pop, "LOCALTTS_PLAY", None)
        cfg = config.load()
        self.assertEqual(cfg["providers"]["llamacpp"]["threads"], 12)
        self.assertIs(cfg["play"], False)

    def test_set_values_rejects_unknown_keys(self):
        with self.assertRaises(TTSError):
            config.set_values(["llamacpp.bogus=1"])
        with self.assertRaises(TTSError):
            config.set_values(["bogus=1"])

    def test_set_values_persists(self):
        config.set_values(["provider=piper", "llamacpp.threads=4"])
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["provider"], "piper")
        self.assertEqual(saved["providers"]["llamacpp"]["threads"], 4)

    def test_llamacpp_chunks_run_concurrently_by_default(self):
        # See the comment above "max_workers" in DEFAULTS for the numbers behind 2:
        # each llama-tts call pays several seconds of fixed startup cost regardless
        # of chunk size, so this is where most of the real-world win comes from.
        self.assertEqual(config.DEFAULTS["providers"]["llamacpp"]["max_workers"], 2)


class MigrationDetectionTest(unittest.TestCase):
    def cfg(self, template, provider="llamacpp"):
        merged = {"provider": provider, "providers": dict(config.DEFAULTS["providers"])}
        merged["providers"] = {k: dict(v) for k, v in merged["providers"].items()}
        merged["providers"]["command"] = {"template": template}
        return merged

    def test_no_command_template_means_nothing_to_migrate(self):
        cfg = self.cfg("")
        self.assertEqual(config.detect_migrations(cfg), [])

    def test_unrelated_template_is_not_flagged(self):
        cfg = self.cfg("espeak-ng -w {output} {text}")
        self.assertEqual(config.detect_migrations(cfg), [])

    def test_the_real_installed_kokoro_wrapper_is_detected(self):
        # The exact template this project's own setup produces.
        cfg = self.cfg("kokoro-tts -o {output} -v ef_dora -l es {text}")
        found = config.detect_migrations(cfg)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["provider"], "kokoro")
        self.assertEqual(found[0]["sets"], {"kokoro.voice": "ef_dora", "kokoro.lang": "es"})

    def test_placeholder_tokens_are_never_captured_as_flag_values(self):
        # A template like `kokoro-tts -v {text} -o {output}` (voice accidentally left as
        # the placeholder) must not migrate kokoro.voice to the literal string "{text}".
        cfg = self.cfg("kokoro-tts -o {output} -v {text}")
        found = config.detect_migrations(cfg)
        self.assertNotIn("kokoro.voice", found[0]["sets"])

    def test_rvc_template_is_detected(self):
        cfg = self.cfg("/venv/bin/python -m rvc_python cli -i {text} -o {output} "
                       "-mp /models/jarvis.pth -de cuda:0")
        found = config.detect_migrations(cfg)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["provider"], "rvc")
        self.assertEqual(found[0]["sets"]["rvc.model"], "/models/jarvis.pth")
        self.assertEqual(found[0]["sets"]["rvc.device"], "cuda:0")

    def test_was_default_flags_only_when_command_is_the_active_provider(self):
        active = self.cfg("kokoro-tts -o {output} -v x {text}", provider="command")
        inactive = self.cfg("kokoro-tts -o {output} -v x {text}", provider="llamacpp")
        self.assertTrue(config.detect_migrations(active)[0]["was_default"])
        self.assertFalse(config.detect_migrations(inactive)[0]["was_default"])

    def test_malformed_template_does_not_raise(self):
        cfg = self.cfg("kokoro-tts 'unterminated")
        self.assertEqual(config.detect_migrations(cfg), [])


class LlamaCppTest(unittest.TestCase):
    def build(self, **settings):
        merged = dict(config.DEFAULTS["providers"]["llamacpp"])
        merged.update(settings)
        provider = LlamaCppProvider(merged)
        provider.resolve_binary = lambda *a, **k: "/usr/bin/llama-tts"
        return provider

    def test_defaults_to_bundled_oute_weights(self):
        cmd = self.build().build_command("hi", "/tmp/a.wav")
        self.assertIn("--tts-oute-default", cmd)
        self.assertEqual(cmd[-4:], ["-p", "hi", "-o", "/tmp/a.wav"])

    def test_model_requires_vocoder(self):
        with tempfile.NamedTemporaryFile(suffix=".gguf") as model:
            with self.assertRaises(TTSError):
                self.build(model=model.name).build_command("hi", "/tmp/a.wav")

    def test_model_and_vocoder_are_passed_through(self):
        with tempfile.NamedTemporaryFile(suffix=".gguf") as model, \
                tempfile.NamedTemporaryFile(suffix=".gguf") as vocoder:
            cmd = self.build(model=model.name, vocoder=vocoder.name).build_command("hi", "/tmp/a.wav")
        self.assertIn("-m", cmd)
        self.assertIn("-mv", cmd)
        self.assertNotIn("--tts-oute-default", cmd)

    def test_missing_model_file_is_reported(self):
        with self.assertRaises(TTSError):
            self.build(model="/definitely/not/here.gguf", vocoder="/nope.gguf").build_command("hi", "/tmp/a.wav")

    def test_max_workers_defaults_to_two_and_is_overridable(self):
        self.assertEqual(self.build().max_workers, 2)
        self.assertEqual(self.build(max_workers=5).max_workers, 5)
        self.assertEqual(self.build(max_workers=0).max_workers, 1)  # clamped to at least 1


class KokoroProviderTest(unittest.TestCase):
    """Targets the real interface: -o/-v/-l/-s flags, text as a trailing positional arg
    (not stdin) -- confirmed against an actual installed `kokoro-tts` wrapper."""

    def build(self, **settings):
        merged = dict(config.DEFAULTS["providers"]["kokoro"])
        merged.update(settings)
        provider = KokoroProvider(merged)
        provider.resolve_binary = lambda *a, **k: "/usr/bin/kokoro-tts"
        return provider

    def test_model_dir_is_optional_by_default(self):
        # Most kokoro CLIs manage their own model location internally; only some
        # (e.g. nazdridoy/kokoro-tts) resolve model files via a working directory.
        cmd = self.build().build_command("hi", "/tmp/a.wav")
        self.assertNotIn("model_dir", " ".join(cmd))

    def test_missing_model_files_are_reported_by_name_when_model_dir_is_set(self):
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaises(TTSError) as caught:
                self.build(model_dir=empty).synthesize("hi", "/tmp/a.wav")
        self.assertIn("kokoro-v1.0.onnx", str(caught.exception))
        self.assertIn("voices-v1.0.bin", str(caught.exception))

    def test_text_is_the_trailing_positional_argument(self):
        cmd = self.build().build_command("hello there", "/tmp/a.wav")
        self.assertEqual(cmd[0], "/usr/bin/kokoro-tts")
        self.assertEqual(cmd[-1], "hello there")

    def test_output_flag(self):
        cmd = self.build().build_command("hi", "/tmp/a.wav")
        self.assertIn("-o", cmd)
        self.assertEqual(cmd[cmd.index("-o") + 1], "/tmp/a.wav")

    def test_voice_and_language_flags(self):
        cmd = self.build(voice="ef_dora", lang="es").build_command("hi", "/tmp/a.wav")
        self.assertIn("-v", cmd)
        self.assertIn("ef_dora", cmd)
        self.assertIn("-l", cmd)
        self.assertIn("es", cmd)

    def test_no_voice_or_lang_flags_when_unconfigured(self):
        cmd = self.build().build_command("hi", "/tmp/a.wav")
        self.assertNotIn("-v", cmd)
        self.assertNotIn("-l", cmd)

    def test_explicit_voice_argument_overrides_the_configured_one(self):
        cmd = self.build(voice="ef_dora").build_command("hi", "/tmp/a.wav", voice="am_adam")
        self.assertIn("am_adam", cmd)
        self.assertNotIn("ef_dora", cmd)

    def test_default_speed_is_omitted_non_default_is_passed(self):
        default_cmd = self.build().build_command("hi", "/tmp/a.wav")
        self.assertNotIn("-s", default_cmd)
        fast_cmd = self.build(speed=1.3).build_command("hi", "/tmp/a.wav")
        self.assertIn("-s", fast_cmd)
        self.assertIn("1.3", fast_cmd)

    def test_check_ok_without_a_model_dir_configured(self):
        ok, message = self.build().check()
        self.assertTrue(ok)

    def test_check_reports_a_bad_model_dir_when_one_is_configured(self):
        ok, message = self.build(model_dir="/definitely/not/here").check()
        self.assertFalse(ok)
        self.assertIn("model_dir", message)

    def test_real_installed_kokoro_wrapper_matches_this_shape(self):
        # The exact command this project's own `local-tts-configure` sets up, and what
        # was previously wired through the generic `command` provider before this
        # provider existed -- see MigrationTest for the detection side of that.
        cmd = self.build(voice="ef_dora", lang="es").build_command("hola", "/tmp/a.wav")
        self.assertEqual(cmd, ["/usr/bin/kokoro-tts", "-o", "/tmp/a.wav",
                              "-v", "ef_dora", "-l", "es", "hola"])


class RvcProviderTest(unittest.TestCase):
    def build(self, cfg=None, **settings):
        merged = dict(config.DEFAULTS["providers"]["rvc"])
        merged.update(settings)
        cfg = cfg if cfg is not None else {"provider": "piper", "providers": config.DEFAULTS["providers"]}
        provider = RvcProvider(merged, cfg=cfg)
        provider._python = lambda: "/usr/bin/python3"
        return provider

    def test_python_interpreter_is_required(self):
        provider = RvcProvider(dict(config.DEFAULTS["providers"]["rvc"]), cfg={})
        with self.assertRaises(TTSError) as caught:
            provider.build_command("in.wav", "out.wav")
        self.assertIn("rvc.python", str(caught.exception))

    def test_model_is_required(self):
        with self.assertRaises(TTSError) as caught:
            self.build().build_command("in.wav", "out.wav")
        self.assertIn("rvc.model", str(caught.exception))

    def test_missing_model_file_is_reported(self):
        with self.assertRaises(TTSError):
            self.build(model="/definitely/not/here.pth").build_command("in.wav", "out.wav")

    def test_full_command_shape(self):
        with tempfile.NamedTemporaryFile(suffix=".pth") as model, \
                tempfile.NamedTemporaryFile(suffix=".index") as index:
            cmd = self.build(model=model.name, index=index.name, device="cuda:0",
                             pitch=2).build_command("in.wav", "out.wav")
        self.assertEqual(cmd[:4], ["/usr/bin/python3", "-m", "rvc_python", "cli"])
        self.assertIn("-i", cmd)
        self.assertIn("in.wav", cmd)
        self.assertIn("-o", cmd)
        self.assertIn("out.wav", cmd)
        self.assertIn("-mp", cmd)
        self.assertIn(model.name, cmd)
        self.assertIn("-ip", cmd)
        self.assertIn(index.name, cmd)
        self.assertIn("-de", cmd)
        self.assertIn("cuda:0", cmd)
        self.assertIn("-pi", cmd)
        self.assertIn("2", cmd)

    def test_default_base_provider_is_the_cfgs_own_default(self):
        cfg = {"provider": "openai", "providers": config.DEFAULTS["providers"]}
        provider = RvcProvider(dict(config.DEFAULTS["providers"]["rvc"]), cfg=cfg)
        self.assertEqual(provider._base_name(), "openai")

    def test_explicit_base_provider_overrides_the_default(self):
        cfg = {"provider": "openai", "providers": config.DEFAULTS["providers"]}
        settings = dict(config.DEFAULTS["providers"]["rvc"])
        settings["base_provider"] = "llamacpp"
        provider = RvcProvider(settings, cfg=cfg)
        self.assertEqual(provider._base_name(), "llamacpp")

    def test_base_provider_cannot_be_rvc_itself(self):
        provider = self.build(base_provider="rvc")
        with self.assertRaises(TTSError) as caught:
            provider.base_provider_instance()
        self.assertIn("itself", str(caught.exception))

    def test_synthesize_chains_the_base_provider_then_converts(self):
        calls = []

        class FakeBase:
            name = "fake"
            default_format = "wav"
            max_words = 0
            max_workers = 1

            def synthesize(self, text, out_path, voice=None):
                calls.append(("base", text, out_path))
                with open(out_path, "wb") as fh:
                    fh.write(b"RIFF....WAVEfake")
                return out_path

        with tempfile.NamedTemporaryFile(suffix=".pth") as model:
            provider = self.build(model=model.name)
            provider.base_provider_instance = lambda: FakeBase()

            def fake_run(cmd, stdin_text=None, cwd=None):
                calls.append(("convert", cmd))
                with open(cmd[cmd.index("-o") + 1], "wb") as fh:
                    fh.write(b"converted")
                return None
            provider.run = fake_run

            provider.synthesize("hello", "/tmp/rvc-out.wav")
        os.unlink("/tmp/rvc-out.wav")

        self.assertEqual(calls[0][0], "base")
        self.assertEqual(calls[1][0], "convert")
        # the temp base wav must not survive the call
        base_wav_path = calls[0][2]
        self.assertFalse(os.path.exists(base_wav_path))

    def test_check_reports_missing_python(self):
        provider = RvcProvider(dict(config.DEFAULTS["providers"]["rvc"]), cfg={})
        ok, message = provider.check()
        self.assertFalse(ok)
        self.assertIn("rvc.python", message)


class CommandProviderTest(unittest.TestCase):
    def test_text_cannot_inject_extra_arguments(self):
        provider = CommandProvider({"template": "espeak-ng -w {output} {text}"})
        cmd = provider.build_command("hi; rm -rf /", "/tmp/a.wav")
        self.assertEqual(cmd, ["espeak-ng", "-w", "/tmp/a.wav", "hi; rm -rf /"])

    def test_check_rejects_a_binary_that_is_not_installed(self):
        provider = CommandProvider({"template": "definitely-not-installed-xyz -w {output} {text}"})
        ok, message = provider.check()
        self.assertFalse(ok)
        self.assertIn("not on PATH", message)

    def test_missing_binary_is_a_clean_error_not_a_traceback(self):
        provider = CommandProvider({"template": "definitely-not-installed-xyz -w {output} {text}"})
        with self.assertRaises(TTSError) as caught:
            provider.synthesize("hola", "/tmp/nope.wav")
        self.assertIn("command not found", str(caught.exception))

    def test_template_must_have_placeholders(self):
        with self.assertRaises(TTSError):
            CommandProvider({"template": "espeak-ng -w {output}"}).build_command("hi", "/tmp/a.wav")


class MarkdownTest(unittest.TestCase):
    def strip(self, raw):
        return textutil.strip_markdown(raw)

    def test_code_fences_are_dropped_entirely(self):
        out = self.strip("Antes.\n\n```python\nprint('no leer')\n```\n\nDespués.")
        self.assertNotIn("print", out)
        self.assertIn("Antes.", out)
        self.assertIn("Después.", out)

    def test_links_keep_their_label_and_lose_the_url(self):
        out = self.strip("Ver [la documentación](https://example.com/x) aquí.")
        self.assertIn("la documentación", out)
        self.assertNotIn("example.com", out)

    def test_emphasis_and_headings_lose_their_markers(self):
        out = self.strip("## Título\n\nCon **negrita** y *cursiva* y `código`.")
        for marker in ("#", "**", "`"):
            self.assertNotIn(marker, out)
        self.assertIn("negrita", out)
        self.assertIn("cursiva", out)

    def test_bullets_and_quotes_lose_their_markers(self):
        out = self.strip("- uno\n- dos\n\n> citado")
        self.assertNotIn("-", out)
        self.assertNotIn(">", out)
        self.assertIn("uno", out)
        self.assertIn("citado", out)

    def test_plain_prose_survives_unchanged(self):
        prose = "Una frase normal, con comas y puntos. Y otra."
        self.assertEqual(self.strip(prose), prose)

    def test_markdown_detection_by_suffix(self):
        self.assertTrue(textutil.looks_like_markdown("/tmp/notes.MD"))
        self.assertFalse(textutil.looks_like_markdown("/tmp/notes.txt"))


class ChunkTest(unittest.TestCase):
    def test_zero_limit_means_one_piece(self):
        self.assertEqual(textutil.chunks("a b c d", 0), ["a b c d"])

    def test_every_piece_respects_the_limit(self):
        prose = " ".join("palabra%d" % i for i in range(200)) + "."
        for piece in textutil.chunks(prose, 25):
            self.assertLessEqual(len(piece.split()), 25)

    def test_no_words_are_lost(self):
        prose = ("Primera frase corta. Segunda frase un poco más larga, con comas. "
                 "Tercera.\n\nOtro párrafo distinto.")
        self.assertEqual(" ".join(textutil.chunks(prose, 6)).split(), prose.split())

    def test_sentences_are_kept_whole_when_they_fit(self):
        pieces = textutil.chunks("Uno dos tres. Cuatro cinco seis. Siete ocho nueve.", 3)
        self.assertEqual(pieces, ["Uno dos tres.", "Cuatro cinco seis.", "Siete ocho nueve."])


class ConcatTest(unittest.TestCase):
    def make(self, path, seconds):
        import wave
        with wave.open(path, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(24000)
            handle.writeframes(b"\x01\x00" * int(24000 * seconds))
        return path

    def test_joined_duration_includes_the_gaps(self):
        with tempfile.TemporaryDirectory() as work:
            parts = [self.make(os.path.join(work, "%d.wav" % i), 1.0) for i in range(3)]
            out = os.path.join(work, "joined.wav")
            audio.concat_wavs(parts, out, gap_seconds=0.5)
            self.assertAlmostEqual(audio.duration(out), 3 * 1.0 + 2 * 0.5, places=2)

    def test_joining_nothing_is_an_error(self):
        with self.assertRaises(TTSError):
            audio.concat_wavs([], "/tmp/never.wav")


def _write_wav(path, marker_byte, frames=50):
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(bytes([marker_byte, 0]) * frames)


class SynthesizeConcurrencyTest(unittest.TestCase):
    """Exercises cli._synthesize's chunk-and-join path directly, without a real backend."""

    def _run(self, provider, text):
        args = types.SimpleNamespace(voice=None)
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.wav")
            _synthesize(provider, text, out, args)
            with wave.open(out, "rb") as handle:
                return handle.readframes(handle.getnframes())

    def test_single_piece_text_skips_the_chunking_machinery(self):
        calls = []

        class RecordingProvider(Provider):
            name = "recording"

            def synthesize(self, text, out_path, voice=None):
                calls.append((text, out_path))
                _write_wav(out_path, 0)
                return out_path

        provider = RecordingProvider({"max_words": 100})
        args = types.SimpleNamespace(voice=None)
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.wav")
            _synthesize(provider, "short text", out, args)
        self.assertEqual(calls, [("short text", out)])

    def test_chunks_run_concurrently_up_to_max_workers(self):
        active, peak = [], [0]
        lock = threading.Lock()

        class SlowProvider(Provider):
            name = "slow"

            def synthesize(self, text, out_path, voice=None):
                with lock:
                    active.append(1)
                    peak[0] = max(peak[0], len(active))
                time.sleep(0.1)
                _write_wav(out_path, 0)
                with lock:
                    active.pop()
                return out_path

        provider = SlowProvider({"max_words": 3, "max_workers": 2})
        text = " ".join("w%d" % i for i in range(12))  # -> 4 chunks of 3 words
        self._run(provider, text)
        self.assertGreater(peak[0], 1, "chunks ran serially -- concurrency isn't happening")
        self.assertLessEqual(peak[0], 2, "max_workers=2 was not respected as a concurrency cap")

    def test_output_order_matches_chunk_order_even_when_finished_out_of_order(self):
        # Chunk 0 is the slow one and finishes last; the join must still put its
        # audio first, because output order is decided by chunk index, not by
        # which worker happens to finish first.
        delays = {0: 0.15, 1: 0.0}

        class ReorderingProvider(Provider):
            name = "reorder"

            def synthesize(self, text, out_path, voice=None):
                index = int(text.split()[0][1:])
                time.sleep(delays.get(index, 0))
                _write_wav(out_path, index)
                return out_path

        provider = ReorderingProvider({"max_words": 3, "max_workers": 2})
        data = self._run(provider, "w0 a b w1 c d")
        self.assertEqual(data[0], 0)

    def test_a_chunk_failure_raises_and_pending_work_is_not_started(self):
        started = []
        lock = threading.Lock()

        class FlakyProvider(Provider):
            name = "flaky"

            def synthesize(self, text, out_path, voice=None):
                index = int(text.split()[0][1:])
                with lock:
                    started.append(index)
                if index == 0:
                    raise TTSError("boom")
                _write_wav(out_path, index)
                return out_path

        # max_workers=1 makes this deterministic: chunks run strictly in order,
        # so the failure on chunk 0 must stop chunk 1 from ever starting.
        provider = FlakyProvider({"max_words": 1, "max_workers": 1})
        with self.assertRaises(TTSError):
            self._run(provider, "w0 w1")
        self.assertEqual(started, [0])


class PlaybackControlTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "playback.json")
        self.original = audio.STATE_FILE
        audio.STATE_FILE = self.state
        self.addCleanup(setattr, audio, "STATE_FILE", self.original)
        self.addCleanup(self.tmp.cleanup)

    def test_idle_reports_nothing_playing(self):
        for call in (audio.playback_status, audio.stop_playback,
                     audio.pause_playback, audio.resume_playback):
            ok, message = call()
            self.assertFalse(ok, call.__name__)
            self.assertIn("nothing is playing", message)

    def test_stale_pid_is_reported_as_idle_and_cleared(self):
        with open(self.state, "w") as fh:
            json.dump({"pid": 2 ** 30, "path": "/tmp/gone.wav"}, fh)
        ok, message = audio.playback_status()
        self.assertFalse(ok)
        self.assertFalse(os.path.exists(self.state))

    def test_control_a_real_background_process(self):
        import subprocess as sp
        proc = sp.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                        stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        self.addCleanup(proc.kill)
        audio._write_state(proc.pid, "/tmp/x.wav")

        ok, message = audio.playback_status()
        self.assertTrue(ok)
        self.assertIn("playing", message)

        if hasattr(signal, "SIGSTOP"):
            self.assertTrue(audio.pause_playback()[0])
            self.assertIn("paused", audio.playback_status()[1])
            self.assertTrue(audio.resume_playback()[0])
            self.assertIn("playing", audio.playback_status()[1])

        ok, message = audio.stop_playback()
        self.assertTrue(ok)
        self.assertIn("stopped", message)
        self.assertFalse(os.path.exists(self.state))
        proc.wait(timeout=5)

    def test_starting_playback_stops_the_previous_one(self):
        import subprocess as sp
        proc = sp.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                        stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        self.addCleanup(proc.kill)
        audio._write_state(proc.pid, "/tmp/old.wav")
        audio.stop_previous()
        proc.wait(timeout=5)
        self.assertFalse(os.path.exists(self.state))

    def test_status_reports_elapsed_and_total_duration(self):
        audio._write_state(os.getpid(), "/tmp/x.wav", duration_seconds=12.0, elapsed=4.0,
                           segment_start=None)
        ok, message = audio.playback_status()
        self.assertTrue(ok)
        self.assertIn("0:04 / 0:12", message)

    def test_elapsed_advances_while_running(self):
        import time as _time
        audio._write_state(os.getpid(), "/tmp/x.wav", duration_seconds=12.0,
                           segment_start=_time.time() - 3.0)
        state = audio.read_state()
        self.assertAlmostEqual(audio._elapsed(state), 3.0, delta=0.5)

    def test_pause_freezes_elapsed_time(self):
        import time as _time
        proc_pid = os.getpid()   # a real, currently-running pid; no signal is actually sent to it here
        audio._write_state(proc_pid, "/tmp/x.wav", duration_seconds=12.0,
                           segment_start=_time.time() - 2.0)
        state = audio.read_state()
        before = audio._elapsed(state)
        # Simulate what pause_playback() records, without sending a real SIGSTOP to this test process.
        audio._write_state(proc_pid, "/tmp/x.wav", duration_seconds=12.0, paused=True,
                           elapsed=before, segment_start=None)
        _time.sleep(0.2)
        after = audio._elapsed(audio.read_state())
        self.assertAlmostEqual(before, after, delta=0.05)


class ProgressBarTest(unittest.TestCase):
    def test_format_time(self):
        self.assertEqual(audio.format_time(0), "0:00")
        self.assertEqual(audio.format_time(65), "1:05")
        self.assertEqual(audio.format_time(-3), "0:00")

    def test_bar_fills_proportionally(self):
        empty = audio.progress_bar(0, 10, width=10)
        half = audio.progress_bar(5, 10, width=10)
        full = audio.progress_bar(10, 10, width=10)
        self.assertEqual(empty.count("#"), 0)
        self.assertEqual(half.count("#"), 5)
        self.assertEqual(full.count("#"), 10)

    def test_bar_never_exceeds_full_past_the_end(self):
        over = audio.progress_bar(999, 10, width=10)
        self.assertEqual(over.count("#"), 10)

    def test_zero_duration_is_handled_without_dividing_by_zero(self):
        bar = audio.progress_bar(3, 0, width=10)
        self.assertIn("0:03", bar)

    def test_elapsed_label_is_clamped_to_the_total_not_just_the_bar_fill(self):
        # Real elapsed time can briefly exceed the file's duration between refreshes --
        # the displayed "X / Y" must never show X > Y, e.g. "0:11 / 0:09".
        bar = audio.progress_bar(11, 9, width=10)
        self.assertEqual(bar, "[##########] 0:09 / 0:09")


class RegistryTest(unittest.TestCase):
    def test_every_registered_provider_has_defaults(self):
        for name in providers.names():
            self.assertIn(name, config.DEFAULTS["providers"], name)
            self.assertIn(name, providers.DESCRIPTIONS, name)

    def test_unknown_provider_raises(self):
        with self.assertRaises(TTSError):
            providers.build("nope", config.DEFAULTS)


class LanguageMemoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["LOCALTTS_CONFIG"] = os.path.join(self.tmp.name, "config.json")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.environ.pop, "LOCALTTS_CONFIG", None)

    def test_record_provider_and_voice(self):
        config.set_values(["languages.es=piper:/voices/es_MX.onnx"])
        entry = config.language_entry(config.load(), "es")
        self.assertEqual(entry, {"provider": "piper", "voice": "/voices/es_MX.onnx"})

    def test_specific_region_wins_over_base(self):
        config.set_values(["languages.es=piper:/generic.onnx",
                           "languages.es-MX=piper:/mexican.onnx"])
        cfg = config.load()
        self.assertEqual(config.language_entry(cfg, "es-MX")["voice"], "/mexican.onnx")
        self.assertEqual(config.language_entry(cfg, "es_mx")["voice"], "/mexican.onnx")
        self.assertEqual(config.language_entry(cfg, "es")["voice"], "/generic.onnx")

    def test_unknown_language_is_absent_not_guessed(self):
        config.set_values(["languages.es=piper"])
        self.assertIsNone(config.language_entry(config.load(), "fr"))

    def test_forget_removes_the_entry(self):
        config.set_values(["languages.es=piper"])
        config.set_values(["languages.es="])
        self.assertEqual(config.load()["languages"], {})

    def test_unknown_provider_is_rejected(self):
        with self.assertRaises(TTSError):
            config.set_values(["languages.es=notaprovider"])

    def test_env_can_record_a_language(self):
        os.environ["LOCALTTS_LANG_DE"] = "piper:/voices/de.onnx"
        self.addCleanup(os.environ.pop, "LOCALTTS_LANG_DE", None)
        self.assertEqual(config.language_entry(config.load(), "de")["provider"], "piper")

    def test_lang_flag_selects_the_recorded_backend(self):
        config.set_values(["languages.es=piper:/voices/es.onnx"])
        self.assertEqual(main(["--lang", "es", "--dry-run", "hola"]), 1)   # piper voice missing -> clean error

    def test_lang_flag_without_a_record_is_a_clean_error(self):
        self.assertEqual(main(["--lang", "xx", "hola"]), 1)

    def test_explicit_provider_beats_the_recorded_one(self):
        config.set_values(["languages.es=piper:/voices/es.onnx"])
        self.assertEqual(main(["--lang", "es", "-p", "llamacpp", "--dry-run", "hola"]), 0)


SAMPLE_RULES = "# Mine\n\nAlways use tabs.\n"


class CompactStatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = audio.STATE_FILE
        audio.STATE_FILE = os.path.join(self.tmp.name, "playback.json")
        self.addCleanup(setattr, audio, "STATE_FILE", self.original)
        self.addCleanup(self.tmp.cleanup)

    def test_empty_when_idle(self):
        self.assertEqual(audio.compact_status(), "")

    def test_non_empty_while_playing(self):
        audio._write_state(os.getpid(), "/tmp/x.wav", duration_seconds=12.0, segment_start=0.0)
        status = audio.compact_status()
        self.assertIn("🔊", status)
        self.assertNotIn("\n", status)

    def test_paused_uses_a_different_icon(self):
        audio._write_state(os.getpid(), "/tmp/x.wav", duration_seconds=12.0, paused=True,
                           elapsed=3.0, segment_start=None)
        self.assertIn("⏸", audio.compact_status())

    def test_elapsed_label_is_clamped_to_the_total(self):
        audio._write_state(os.getpid(), "/tmp/x.wav", duration_seconds=9.0,
                           elapsed=11.0, segment_start=None)
        status = audio.compact_status()
        self.assertIn("0:09/0:09", status)
        self.assertNotIn("0:11", status)


class SessionScopingTest(unittest.TestCase):
    """State files, and therefore playback control, are isolated per session so two
    concurrent sessions (two terminals, two agents) don't stop or read each other's audio."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = audio.STATE_FILE
        audio.STATE_FILE = os.path.join(self.tmp.name, "playback.json")
        self.addCleanup(setattr, audio, "STATE_FILE", self.original)
        self.addCleanup(self.tmp.cleanup)

    def test_no_session_uses_the_original_global_file(self):
        self.assertEqual(audio.state_path(), audio.STATE_FILE)
        self.assertEqual(audio.state_path(None), audio.STATE_FILE)

    def test_different_sessions_get_different_files(self):
        a = audio.state_path("session-a")
        b = audio.state_path("session-b")
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, audio.STATE_FILE)

    def test_same_session_string_is_deterministic(self):
        self.assertEqual(audio.state_path("same"), audio.state_path("same"))

    def test_session_path_is_sanitized_and_bounded(self):
        path = audio.state_path("weird/../id with spaces\x00" + "x" * 500)
        self.assertNotIn("/", os.path.basename(path))
        self.assertLess(len(os.path.basename(path)), 120)

    def test_a_second_sessions_playback_does_not_stop_the_firsts(self):
        import subprocess as sp
        proc_a = sp.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                          stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        self.addCleanup(proc_a.kill)
        audio._write_state(proc_a.pid, "/tmp/a.wav", session="session-a")

        # Starting session B's playback must not touch session A's state or process.
        audio.stop_previous(session="session-b")

        self.assertTrue(audio.is_running(proc_a.pid))
        ok, _ = audio.playback_status(session="session-a")
        self.assertTrue(ok)

    def test_stop_only_affects_its_own_session(self):
        import subprocess as sp
        proc_a = sp.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                          stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        self.addCleanup(proc_a.kill)
        audio._write_state(proc_a.pid, "/tmp/a.wav", session="session-a")

        ok, message = audio.stop_playback(session="session-b")
        self.assertFalse(ok)
        self.assertIn("nothing is playing", message)
        self.assertTrue(audio.is_running(proc_a.pid))

        ok, _ = audio.stop_playback(session="session-a")
        self.assertTrue(ok)
        proc_a.wait(timeout=5)

    def test_compact_status_is_isolated_per_session(self):
        audio._write_state(os.getpid(), "/tmp/a.wav", duration_seconds=10.0,
                           segment_start=0.0, session="session-a")
        self.assertNotEqual(audio.compact_status(session="session-a"), "")
        self.assertEqual(audio.compact_status(session="session-b"), "")
        self.assertEqual(audio.compact_status(), "")   # the global slot is untouched


class SessionResolutionTest(unittest.TestCase):
    """_resolve_session() is where env-var auto-detection lives -- audio.py itself never
    reads the environment, so these tests must not let a real ambient session id (this
    suite runs inside an actual Claude Code session) leak into assertions about the
    no-session fallback."""

    def setUp(self):
        self.saved = {name: os.environ.pop(name, None) for name in
                      ("CLAUDE_CODE_SESSION_ID",)}
        self.addCleanup(self._restore)

    def _restore(self):
        for name, value in self.saved.items():
            if value is not None:
                os.environ[name] = value
            else:
                os.environ.pop(name, None)

    def test_no_signal_at_all_resolves_to_none(self):
        self.assertIsNone(_resolve_session(None))

    def test_explicit_flag_wins(self):
        os.environ["CLAUDE_CODE_SESSION_ID"] = "from-env"
        self.assertEqual(_resolve_session("from-flag"), "from-flag")

    def test_env_var_is_used_when_no_flag(self):
        os.environ["CLAUDE_CODE_SESSION_ID"] = "from-env"
        self.assertEqual(_resolve_session(None), "from-env")

    def test_stdin_json_session_id_beats_env(self):
        os.environ["CLAUDE_CODE_SESSION_ID"] = "from-env"
        stdin = json.dumps({"session_id": "from-json"})
        self.assertEqual(_resolve_session(None, stdin_json=stdin), "from-json")

    def test_stdin_json_sessionId_camelcase_is_also_recognized(self):
        stdin = json.dumps({"sessionId": "from-json-camel"})
        self.assertEqual(_resolve_session(None, stdin_json=stdin), "from-json-camel")

    def test_malformed_stdin_json_falls_back_to_env(self):
        os.environ["CLAUDE_CODE_SESSION_ID"] = "from-env"
        self.assertEqual(_resolve_session(None, stdin_json="not json"), "from-env")

    def test_explicit_flag_beats_stdin_json_too(self):
        stdin = json.dumps({"session_id": "from-json"})
        self.assertEqual(_resolve_session("from-flag", stdin_json=stdin), "from-flag")


class BackgroundSessionCliTest(unittest.TestCase):
    """End-to-end through main(): starting and controlling playback for two sessions
    without either one's global env leaking into the other's isolation."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = audio.STATE_FILE
        audio.STATE_FILE = os.path.join(self.tmp.name, "playback.json")
        self.addCleanup(setattr, audio, "STATE_FILE", self.original)
        self.addCleanup(self.tmp.cleanup)
        self.saved_env = os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self.saved_env is not None:
            os.environ["CLAUDE_CODE_SESSION_ID"] = self.saved_env

    def test_compact_reads_session_from_piped_json(self):
        import io
        from unittest import mock
        audio._write_state(os.getpid(), "/tmp/x.wav", duration_seconds=10.0,
                           segment_start=0.0, session="from-hook-json")
        stdin = io.StringIO(json.dumps({"session_id": "from-hook-json"}))
        stdin.isatty = lambda: False
        captured = io.StringIO()
        with mock.patch.object(sys, "stdin", stdin), mock.patch.object(sys, "stdout", captured):
            self.assertEqual(main(["playback", "--compact"]), 0)
        self.assertIn("🔊", captured.getvalue())

    def test_compact_is_empty_for_a_session_with_no_state(self):
        import io
        from unittest import mock
        audio._write_state(os.getpid(), "/tmp/x.wav", duration_seconds=10.0,
                           segment_start=0.0, session="playing-session")
        stdin = io.StringIO(json.dumps({"session_id": "some-other-session"}))
        stdin.isatty = lambda: False
        captured = io.StringIO()
        with mock.patch.object(sys, "stdin", stdin), mock.patch.object(sys, "stdout", captured):
            self.assertEqual(main(["playback", "--compact"]), 0)
        self.assertEqual(captured.getvalue(), "")

    def test_explicit_session_flag_on_stop(self):
        import subprocess as sp
        proc = sp.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                        stdout=sp.DEVNULL, stderr=sp.DEVNULL)
        self.addCleanup(proc.kill)
        audio._write_state(proc.pid, "/tmp/x.wav", session="explicit-session")
        self.assertEqual(main(["stop", "--session", "explicit-session"]), 0)
        proc.wait(timeout=5)


class HookInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name
        os.makedirs(os.path.join(self.base, ".claude"))
        self.addCleanup(self.tmp.cleanup)

    def _settings(self):
        with open(hooks.settings_path("claude-code", self.base)) as fh:
            return json.load(fh)

    def _existing_script(self, name="other-tool.sh", body="#!/usr/bin/env bash\necho hi\n"):
        path = Path(self.base) / ".claude" / name
        path.write_text(body)
        path.chmod(0o755)
        return path

    def test_unsupported_agent_reports_why(self):
        with self.assertRaises(TTSError) as caught:
            hooks.install("gemini", base=self.base)
        self.assertIn("footer", str(caught.exception))

    def test_fresh_install_with_nothing_configured_writes_a_standalone_wrapper(self):
        result = hooks.install("claude-code", base=self.base)
        self.assertEqual(result["mode"], "standalone")
        settings = self._settings()
        self.assertEqual(settings["statusLine"]["command"], str(result["wrapper_path"]))
        self.assertEqual(settings["statusLine"]["refreshInterval"], 2)
        self.assertTrue(result["wrapper_path"].exists())

    # --- the regression this class exists for -------------------------------------
    #
    # A real Boost installation broke because install() used to REPLACE
    # statusLine.command with our own wrapper and remember the old command as a string
    # to chain via `eval`. When Boost's own reinstall regenerated its script at the same
    # path, our saved reference still pointed at the right path in principle, but Boost's
    # own installer no longer recognized statusLine as its own (something else -- us --
    # now owned it) and declined to re-register itself, leaving the whole status line
    # broken until the user manually reinstalled Boost. The fix: never touch
    # statusLine.command when one is already configured; append into the file it already
    # points to instead, so the original tool never stops owning its own slot.

    def test_existing_status_line_command_is_never_rewritten(self):
        script = self._existing_script()
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({
            "otherSetting": "keep-me",
            "statusLine": {"type": "command", "command": str(script), "padding": 0},
        }))
        before = self._settings()["statusLine"]
        hooks.install("claude-code", base=self.base)
        after = self._settings()["statusLine"]
        self.assertEqual(before, after)   # byte-for-byte: command, padding, everything
        self.assertEqual(self._settings()["otherSetting"], "keep-me")

    def test_existing_script_file_gets_our_block_appended(self):
        script = self._existing_script(body="#!/usr/bin/env bash\nprintf 'BOOST'\n")
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({"statusLine": {"type": "command", "command": str(script)}}))
        result = hooks.install("claude-code", base=self.base)
        self.assertEqual(result["mode"], "appended")
        content = script.read_text()
        self.assertIn("printf 'BOOST'", content)          # original untouched
        self.assertIn(hooks.HOOK_BEGIN, content)
        self.assertIn("tts playback --compact", content)

    def test_appended_output_concatenates_with_the_original_at_runtime(self):
        import subprocess as sp
        script = self._existing_script(body="#!/usr/bin/env bash\nprintf 'BOOST-OUTPUT'\n")
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({"statusLine": {"type": "command", "command": str(script)}}))
        hooks.install("claude-code", base=self.base)
        out = sp.run(["bash", str(script)], input="{}", capture_output=True, text=True)
        self.assertEqual(out.stdout, "BOOST-OUTPUT")   # idle: appended block adds nothing

    def test_reinstall_does_not_duplicate_the_appended_block(self):
        script = self._existing_script()
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({"statusLine": {"type": "command", "command": str(script)}}))
        hooks.install("claude-code", base=self.base)
        hooks.install("claude-code", base=self.base)
        self.assertEqual(script.read_text().count(hooks.HOOK_BEGIN), 1)

    def test_uninstall_removes_only_our_block_from_the_appended_file(self):
        script = self._existing_script(body="#!/usr/bin/env bash\necho original\n")
        original = script.read_text()
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({"statusLine": {"type": "command", "command": str(script)}}))
        hooks.install("claude-code", base=self.base)
        result = hooks.uninstall("claude-code", base=self.base)
        self.assertTrue(result["removed"])
        self.assertEqual(script.read_text(), original)
        # and the settings.json command was never touched in the first place
        self.assertEqual(self._settings()["statusLine"]["command"], str(script))

    def test_unappendable_command_requires_force(self):
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({
            "statusLine": {"type": "command", "command": "node ~/status.js --flag"},
        }))
        with self.assertRaises(TTSError) as caught:
            hooks.install("claude-code", base=self.base)
        self.assertIn("--force", str(caught.exception))
        # and it must not have touched anything on the refusal path
        self.assertEqual(self._settings()["statusLine"]["command"], "node ~/status.js --flag")

    def test_force_replaces_an_unappendable_command_and_chains_it(self):
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({
            "statusLine": {"type": "command", "command": "node ~/status.js --flag"},
        }))
        result = hooks.install("claude-code", base=self.base, force=True)
        self.assertEqual(result["mode"], "forced")
        self.assertEqual(result["chained_from"], "node ~/status.js --flag")
        self.assertEqual(self._settings()["statusLine"]["command"], str(result["wrapper_path"]))
        self.assertIn("PREV_CMD='node ~/status.js --flag'", result["wrapper_path"].read_text())

    def test_missing_script_file_also_requires_force(self):
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({
            "statusLine": {"type": "command", "command": str(Path(self.base) / ".claude" / "gone.sh")},
        }))
        with self.assertRaises(TTSError):
            hooks.install("claude-code", base=self.base)

    def test_dry_run_writes_nothing_standalone(self):
        result = hooks.install("claude-code", base=self.base, dry_run=True)
        self.assertFalse(result["wrapper_path"].exists())
        self.assertFalse(hooks.settings_path("claude-code", self.base).exists())

    def test_dry_run_writes_nothing_appended(self):
        script = self._existing_script()
        original = script.read_text()
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({"statusLine": {"type": "command", "command": str(script)}}))
        hooks.install("claude-code", base=self.base, dry_run=True)
        self.assertEqual(script.read_text(), original)

    def test_appended_mode_leaves_refresh_interval_alone_by_default(self):
        script = self._existing_script()
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({"statusLine": {"type": "command", "command": str(script)}}))
        result = hooks.install("claude-code", base=self.base)
        self.assertIsNone(result.get("refresh_interval"))
        self.assertNotIn("refreshInterval", self._settings()["statusLine"])

    def test_appended_mode_sets_refresh_interval_only_when_explicitly_asked(self):
        script = self._existing_script()
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({
            "otherSetting": "keep-me",
            "statusLine": {"type": "command", "command": str(script)},
        }))
        result = hooks.install("claude-code", base=self.base, refresh_interval=5)
        self.assertEqual(result["refresh_interval"], 5)
        settings = self._settings()
        self.assertEqual(settings["statusLine"]["refreshInterval"], 5)
        self.assertEqual(settings["statusLine"]["command"], str(script))   # command still untouched
        self.assertEqual(settings["otherSetting"], "keep-me")

    def test_explicit_refresh_interval_dry_run_writes_nothing(self):
        script = self._existing_script()
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({"statusLine": {"type": "command", "command": str(script)}}))
        hooks.install("claude-code", base=self.base, refresh_interval=5, dry_run=True)
        self.assertNotIn("refreshInterval", self._settings()["statusLine"])

    def test_zero_means_explicitly_event_based_in_appended_mode(self):
        script = self._existing_script()
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({
            "statusLine": {"type": "command", "command": str(script), "refreshInterval": 5},
        }))
        result = hooks.install("claude-code", base=self.base, refresh_interval=0)
        self.assertEqual(result["refresh_interval"], 0)
        self.assertTrue(result["settings_changed"])
        settings = self._settings()
        self.assertNotIn("refreshInterval", settings["statusLine"])
        self.assertEqual(settings["statusLine"]["command"], str(script))   # command untouched

    def test_zero_on_an_already_event_based_config_makes_no_write(self):
        script = self._existing_script()
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({"statusLine": {"type": "command", "command": str(script)}}))
        mtime_before = path.stat().st_mtime_ns
        result = hooks.install("claude-code", base=self.base, refresh_interval=0)
        self.assertEqual(result["refresh_interval"], 0)
        self.assertFalse(result["settings_changed"])
        self.assertEqual(path.stat().st_mtime_ns, mtime_before)   # never even opened for write

    def test_zero_means_explicitly_event_based_in_standalone_mode(self):
        result = hooks.install("claude-code", base=self.base, refresh_interval=0)
        self.assertEqual(result["refresh_interval"], 0)
        self.assertNotIn("refreshInterval", self._settings()["statusLine"])

    def test_no_flag_still_defaults_standalone_to_two_seconds(self):
        result = hooks.install("claude-code", base=self.base)
        self.assertEqual(result["refresh_interval"], 2)
        self.assertEqual(self._settings()["statusLine"]["refreshInterval"], 2)

    def test_uninstall_drops_the_key_when_there_was_nothing_before(self):
        hooks.install("claude-code", base=self.base)
        hooks.uninstall("claude-code", base=self.base)
        self.assertNotIn("statusLine", self._settings())

    def test_uninstall_refuses_to_touch_a_hook_it_did_not_install(self):
        script = self._existing_script()
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({"statusLine": {"type": "command", "command": str(script)}}))
        result = hooks.uninstall("claude-code", base=self.base)
        self.assertFalse(result["removed"])
        self.assertEqual(self._settings()["statusLine"]["command"], str(script))

    def test_is_installed_true_only_for_our_own_wrapper_or_block(self):
        self.assertFalse(hooks.is_installed("claude-code", base=self.base))
        hooks.install("claude-code", base=self.base)
        self.assertTrue(hooks.is_installed("claude-code", base=self.base))

    def test_is_installed_true_for_appended_mode_too(self):
        script = self._existing_script()
        path = hooks.settings_path("claude-code", self.base)
        path.write_text(json.dumps({"statusLine": {"type": "command", "command": str(script)}}))
        self.assertFalse(hooks.is_installed("claude-code", base=self.base))
        hooks.install("claude-code", base=self.base)
        self.assertTrue(hooks.is_installed("claude-code", base=self.base))

    def test_qwen_uses_its_own_nested_key_and_settings_file(self):
        os.makedirs(os.path.join(self.base, ".qwen"))
        hooks.install("qwen", base=self.base)
        with open(hooks.settings_path("qwen", self.base)) as fh:
            settings = json.load(fh)
        self.assertIn("command", settings["ui"]["statusLine"])

    def test_detect_only_returns_agents_actually_present(self):
        self.assertEqual(hooks.detect(base=self.base), {"claude-code": Path(self.base) / ".claude"})


class HooksCliFlagTest(unittest.TestCase):
    def test_refresh_interval_out_of_range_is_rejected(self):
        self.assertEqual(main(["hooks", "--install", "--refresh-interval", "61"]), 1)
        self.assertEqual(main(["hooks", "--install", "--refresh-interval", "-1"]), 1)

    def test_zero_is_a_valid_refresh_interval(self):
        with tempfile.TemporaryDirectory() as base:
            os.makedirs(os.path.join(base, ".claude"))
            from unittest import mock
            with mock.patch.object(hooks, "home", lambda b=None: Path(base)):
                self.assertEqual(
                    main(["hooks", "--install", "claude-code", "--refresh-interval", "0"]), 0)
                with open(hooks.settings_path("claude-code")) as fh:
                    settings = json.load(fh)
                self.assertNotIn("refreshInterval", settings["statusLine"])


class HookLivenessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name
        os.makedirs(os.path.join(self.base, ".claude"))
        self.addCleanup(self.tmp.cleanup)

    def test_inactive_with_no_heartbeat(self):
        self.assertFalse(hooks.is_active("claude-code", base=self.base))
        self.assertFalse(hooks.any_active(base=self.base))

    def test_active_with_a_fresh_heartbeat(self):
        hooks.install("claude-code", base=self.base)
        hb = hooks.heartbeat_path("claude-code", base=self.base)
        hb.parent.mkdir(parents=True, exist_ok=True)
        hb.write_text(str(int(__import__("time").time())))
        self.assertTrue(hooks.is_active("claude-code", base=self.base))
        self.assertTrue(hooks.any_active(base=self.base))

    def test_inactive_with_a_stale_heartbeat(self):
        hooks.install("claude-code", base=self.base)
        hb = hooks.heartbeat_path("claude-code", base=self.base)
        hb.parent.mkdir(parents=True, exist_ok=True)
        hb.write_text(str(int(__import__("time").time()) - 999))
        self.assertFalse(hooks.is_active("claude-code", base=self.base))

    def test_installed_but_never_run_is_not_active(self):
        hooks.install("claude-code", base=self.base)   # config written, wrapper never executed
        self.assertTrue(hooks.is_installed("claude-code", base=self.base))
        self.assertFalse(hooks.is_active("claude-code", base=self.base))


class SkillInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def test_bundled_skills_have_name_and_description(self):
        for name in skills.SKILLS:
            meta, prose = skills.split_frontmatter(skills.read_skill(name))
            self.assertEqual(meta.get("name"), name)
            self.assertTrue(meta.get("description"), name)
            self.assertTrue(prose.strip(), name)

    def test_detection_only_reports_directories_that_exist(self):
        self.assertEqual(skills.detect(self.base), {})
        os.makedirs(os.path.join(self.base, ".claude"))
        self.assertEqual(sorted(skills.detect(self.base)), ["claude-code"])

    def test_skill_shaped_agent_gets_one_file_per_skill(self):
        os.makedirs(os.path.join(self.base, ".claude"))
        written = skills.install("claude-code", base=self.base)
        self.assertEqual(len(written), len(skills.SKILLS))
        for path in written:
            self.assertTrue(path.exists())
            self.assertEqual(path.name, "SKILL.md")
        self.assertEqual(skills.status("claude-code", base=self.base)[0], True)

    def test_doc_shaped_agent_preserves_existing_content(self):
        os.makedirs(os.path.join(self.base, ".codex"))
        target = os.path.join(self.base, ".codex", "AGENTS.md")
        with open(target, "w") as fh:
            fh.write(SAMPLE_RULES)
        skills.install("codex", base=self.base)
        body = open(target).read()
        self.assertIn("Always use tabs.", body)
        self.assertIn(skills.BEGIN, body)

    def test_reinstall_does_not_duplicate_the_section(self):
        os.makedirs(os.path.join(self.base, ".codex"))
        skills.install("codex", base=self.base)
        first = open(os.path.join(self.base, ".codex", "AGENTS.md")).read()
        skills.install("codex", base=self.base)
        second = open(os.path.join(self.base, ".codex", "AGENTS.md")).read()
        self.assertEqual(first, second)
        self.assertEqual(second.count(skills.BEGIN), 1)

    def test_uninstall_restores_the_original_file(self):
        os.makedirs(os.path.join(self.base, ".codex"))
        target = os.path.join(self.base, ".codex", "AGENTS.md")
        with open(target, "w") as fh:
            fh.write(SAMPLE_RULES)
        skills.install("codex", base=self.base)
        skills.uninstall("codex", base=self.base)
        self.assertEqual(open(target).read().strip(), SAMPLE_RULES.strip())

    def test_uninstall_removes_a_file_it_created_alone(self):
        os.makedirs(os.path.join(self.base, ".codex"))
        skills.install("codex", base=self.base)
        skills.uninstall("codex", base=self.base)
        self.assertFalse(os.path.exists(os.path.join(self.base, ".codex", "AGENTS.md")))

    def test_dry_run_writes_nothing(self):
        os.makedirs(os.path.join(self.base, ".claude"))
        written = skills.install("claude-code", base=self.base, dry_run=True)
        self.assertTrue(written)
        for path in written:
            self.assertFalse(path.exists())

    def test_unknown_agent_raises(self):
        with self.assertRaises(TTSError):
            skills.install("notanagent", base=self.base)

    def test_config_root_follows_the_platform(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/xdg"}, clear=False):
            self.assertEqual(str(config.config_root()), "/xdg")
        env = {k: v for k, v in os.environ.items() if k != "XDG_CONFIG_HOME"}
        env["APPDATA"] = os.path.join("C:", "Users", "x", "AppData", "Roaming")
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(sys, "platform", "win32"):
            self.assertEqual(str(config.config_root()), env["APPDATA"])
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(sys, "platform", "linux"):
            self.assertTrue(str(config.config_root()).endswith(".config"))

    def test_skills_and_cli_agree_on_the_config_root(self):
        self.assertEqual(skills.config_root(), config.config_root())

    def test_config_placeholder_is_expanded(self):
        path = skills.resolve("${CONFIG}/opencode", self.base)
        self.assertEqual(path, Path(self.base) / ".config" / "opencode")

    def test_every_agent_has_a_marker_and_a_label(self):
        self.assertEqual(sorted(skills.AGENTS), sorted(skills.MARKERS))
        for name, (kind, relative, label) in skills.AGENTS.items():
            self.assertIn(kind, ("skill", "doc"))
            self.assertTrue(relative and label, name)


class CliTest(unittest.TestCase):
    def test_no_arguments_at_a_prompt_prints_the_help(self):
        import contextlib, io
        from unittest import mock
        captured = io.StringIO()
        with mock.patch.object(sys.stdin, "isatty", return_value=True), \
                contextlib.redirect_stdout(captured):
            self.assertEqual(main([]), 0)
        printed = captured.getvalue()
        self.assertIn("usage:", printed)
        for flag in ("--provider", "--output", "--markdown", "--dry-run"):
            self.assertIn(flag, printed)

    def test_piped_input_still_works_without_arguments(self):
        import io
        from unittest import mock
        with mock.patch.object(sys, "stdin", io.StringIO("")):
            self.assertEqual(main([]), 1)   # empty pipe -> error, not help

    def test_empty_input_exits_nonzero(self):
        self.assertEqual(main(["--file", "/dev/null"]), 1)

    def test_providers_subcommand(self):
        self.assertEqual(main(["providers"]), 0)

    def test_skills_subcommand_reports_status(self):
        self.assertEqual(main(["skills"]), 0)

    def test_skills_print_outputs_the_bundled_skill_verbatim(self):
        import io
        from unittest import mock
        captured = io.StringIO()
        with mock.patch.object(sys, "stdout", captured):
            self.assertEqual(main(["skills", "--print", "local-tts-update"]), 0)
        self.assertEqual(captured.getvalue(), skills.read_skill("local-tts-update"))

    def test_skills_print_rejects_an_unknown_name(self):
        self.assertEqual(main(["skills", "--print", "not-a-real-skill"]), 1)

    def test_skills_print_cannot_combine_with_install(self):
        self.assertEqual(main(["skills", "--print", "local-tts-update", "--install"]), 1)

    def test_languages_subcommand(self):
        self.assertEqual(main(["languages"]), 0)


if __name__ == "__main__":
    unittest.main()
