"""Stdlib-only tests: python -m unittest discover -s tests"""

import json
import os
import signal
import sys
import tempfile
import unittest
from pathlib import Path

from localtts import audio, config, providers, skills, text as textutil
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

    def test_languages_subcommand(self):
        self.assertEqual(main(["languages"]), 0)


if __name__ == "__main__":
    unittest.main()
