"""Stdlib-only tests: python -m unittest discover -s tests"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from localtts import config, providers
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

    def test_template_must_have_placeholders(self):
        with self.assertRaises(TTSError):
            CommandProvider({"template": "espeak-ng -w {output}"}).build_command("hi", "/tmp/a.wav")


class RegistryTest(unittest.TestCase):
    def test_every_registered_provider_has_defaults(self):
        for name in providers.names():
            self.assertIn(name, config.DEFAULTS["providers"], name)
            self.assertIn(name, providers.DESCRIPTIONS, name)

    def test_unknown_provider_raises(self):
        with self.assertRaises(TTSError):
            providers.build("nope", config.DEFAULTS)


class CliTest(unittest.TestCase):
    def test_empty_input_exits_nonzero(self):
        self.assertEqual(main(["--file", "/dev/null"]), 1)

    def test_providers_subcommand(self):
        self.assertEqual(main(["providers"]), 0)


if __name__ == "__main__":
    unittest.main()
