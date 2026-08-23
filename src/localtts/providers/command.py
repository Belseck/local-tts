"""Escape hatch: drive any TTS binary through a configurable template."""

import os
import shlex

from localtts.errors import TTSError
from localtts.providers.base import Provider


class CommandProvider(Provider):
    name = "command"
    default_format = "wav"

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
        return True, template
