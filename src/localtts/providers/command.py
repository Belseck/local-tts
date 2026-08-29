"""Escape hatch: drive any TTS binary through a configurable template."""

import os
import shlex
import shutil

from localtts.errors import TTSError
from localtts.providers.base import Provider


class CommandProvider(Provider):
    name = "command"
    default_format = "wav"

    @property
    def supports_tone_tags(self):
        """False by default -- <tag> markup is stripped before the command ever sees it,
        same as any other backend with no real tone/emotion hook -- unless
        `command.tone_tags=pass`. Local-tts can't know whether an arbitrary shell script
        understands the markup, so unlike every other provider here this is a setting the
        user chooses, not a capability this file verified."""
        return self.settings.get("tone_tags") == "pass"

    def build_command(self, text, out_path):
        template = self.settings.get("template") or ""
        if not template:
            raise TTSError(
                'command.template is empty, e.g. `tts config --set '
                'command.template="espeak-ng -w {output} {text}"`'
            )
        # Split first, substitute after: the text never re-enters the parser,
        # so quotes and spaces in it can't inject extra arguments.
        parts = shlex.split(template)
        if "{output}" not in template:
            raise TTSError("command.template must contain {output}")
        if "{text}" not in template:
            raise TTSError("command.template must contain {text}")
        return [part.replace("{output}", out_path).replace("{text}", text) for part in parts]

    def synthesize(self, text, out_path, voice=None):
        cmd = self.build_command(text, out_path)
        self.run(cmd)
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise TTSError("%s wrote no audio to %s" % (cmd[0], out_path))
        return out_path

    def check(self):
        template = self.settings.get("template") or ""
        if not template:
            return False, "no template configured"
        try:
            executable = self.build_command("probe", "/tmp/probe.wav")[0]
        except TTSError as exc:
            return False, str(exc)
        if not shutil.which(executable):
            return False, "%s (%r is not on PATH)" % (template, executable)
        return True, template
