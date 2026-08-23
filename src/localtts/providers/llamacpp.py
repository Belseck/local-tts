"""Default backend: llama.cpp's `llama-tts` binary (OuteTTS + WavTokenizer)."""

import os

from localtts.errors import TTSError
from localtts.providers.base import Provider


class LlamaCppProvider(Provider):
    name = "llamacpp"
    default_format = "wav"

    def build_command(self, text, out_path, voice=None):
        exe = self.resolve_binary("binary", "llama-tts")
        cmd = [exe]

        model = self.path("model")
        vocoder = self.path("vocoder")
        hf_repo = self.settings.get("hf_repo") or ""

        if model:
            if not os.path.exists(model):
                raise TTSError("model file not found: %s" % model)
            if not vocoder:
                raise TTSError(
                    "llamacpp.model is set but llamacpp.vocoder is not; llama-tts needs both "
                    "(the TTS model and the WavTokenizer vocoder)."
                )
            if not os.path.exists(vocoder):
                raise TTSError("vocoder file not found: %s" % vocoder)
            cmd += ["-m", model, "-mv", vocoder]
        elif hf_repo:
            cmd += ["-hf", hf_repo]
            if self.settings.get("hf_file"):
                cmd += ["-hff", self.settings["hf_file"]]
            if self.settings.get("hf_repo_vocoder"):
                cmd += ["-hfv", self.settings["hf_repo_vocoder"]]
            if self.settings.get("hf_file_vocoder"):
                cmd += ["-hffv", self.settings["hf_file_vocoder"]]
        else:
            # Zero-config path: llama.cpp downloads and caches OuteTTS itself.
            cmd += ["--tts-oute-default"]

        speaker = os.path.expanduser(voice or self.settings.get("speaker_file") or "")
        if speaker:
            if not os.path.exists(speaker):
                raise TTSError("speaker file not found: %s" % speaker)
            cmd += ["--tts-speaker-file", speaker]

        threads = int(self.settings.get("threads") or 0)
        if threads > 0:
            cmd += ["-t", str(threads)]

        gpu_layers = self.settings.get("gpu_layers")
        if gpu_layers is not None:
            cmd += ["-ngl", str(int(gpu_layers))]

        if self.settings.get("guide_tokens"):
            cmd += ["--tts-use-guide-tokens"]

        cmd += list(self.settings.get("extra_args") or [])
        cmd += ["-p", text, "-o", out_path]
        return cmd

    def synthesize(self, text, out_path, voice=None):
        cmd = self.build_command(text, out_path, voice)
        self.run(cmd)
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise TTSError("llama-tts reported success but wrote no audio to %s" % out_path)
        return out_path

    def check(self):
        try:
            exe = self.resolve_binary("binary", "llama-tts")
        except TTSError as exc:
            return False, str(exc)
        model = self.path("model")
        if model and not os.path.exists(model):
            return False, "%s (model missing: %s)" % (exe, model)
        vocoder = self.path("vocoder")
        if model and not vocoder:
            return False, "%s (model set without a vocoder)" % exe
        if vocoder and not os.path.exists(vocoder):
            return False, "%s (vocoder missing: %s)" % (exe, vocoder)
        weights = model or self.settings.get("hf_repo") or "default OuteTTS (downloaded on first run)"
        return True, "%s -> %s" % (exe, weights)
