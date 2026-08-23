"""Stdlib-only tests: python -m unittest discover -s tests"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from localtts import audio, config, providers, text as textutil
from localtts.cli import main
from localtts.errors import TTSError
from localtts.providers.command import CommandProvider
from localtts.providers.llamacpp import LlamaCppProvider


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


class RegistryTest(unittest.TestCase):
    def test_every_registered_provider_has_defaults(self):
        for name in providers.names():
            self.assertIn(name, config.DEFAULTS["providers"], name)
            self.assertIn(name, providers.DESCRIPTIONS, name)

    def test_unknown_provider_raises(self):
        with self.assertRaises(TTSError):
            providers.build("nope", config.DEFAULTS)


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


if __name__ == "__main__":
    unittest.main()
