"""Command-line entry point."""

import argparse
import json
import os
import sys
import tempfile

from localtts import __version__, audio, config, providers
from localtts.errors import TTSError

PROG = "tts"
SUBCOMMANDS = ("config", "providers", "check")


def _speak_parser():
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Speak text with a local TTS model (llama.cpp by default).",
        epilog=(
            "subcommands:\n"
            "  %(prog)s providers            list available backends\n"
            "  %(prog)s check                verify backends and audio players\n"
            "  %(prog)s config --show        print the effective configuration\n"
            "  %(prog)s config --set k=v     persist a setting\n"
            "\nexamples:\n"
            "  %(prog)s \"hello world\"\n"
            "  %(prog)s -o out.wav -f script.txt\n"
            "  echo \"from a pipe\" | %(prog)s\n"
            "  %(prog)s --provider openai --voice nova \"hi there\"\n"
        ) % {"prog": PROG},
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("text", nargs="*", help="text to speak (or pipe it on stdin)")
    parser.add_argument("-f", "--file", help="read the text from a file ('-' for stdin)")
    parser.add_argument("-o", "--output", help="write audio here instead of playing it")
    parser.add_argument("-p", "--provider", choices=providers.names(), help="backend to use")
    parser.add_argument("-v", "--voice", help="voice: speaker file (llamacpp), .onnx (piper) or name (openai)")
    parser.add_argument("-m", "--model", help="override the provider's model for this run")
    parser.add_argument("-s", "--set", dest="overrides", action="append", default=[],
                        metavar="KEY=VALUE", help="override a provider setting for this run (repeatable)")
    parser.add_argument("--play", action="store_true", help="play the audio even when --output is given")
    parser.add_argument("--no-play", action="store_true", help="never play, just report the file path")
    parser.add_argument("--player", help="playback command to use (default: autodetect)")
    parser.add_argument("--keep", action="store_true", help="keep the temporary file and print its path")
    parser.add_argument("--dry-run", action="store_true", help="print the command that would run, then exit")
    parser.add_argument("--verbose", action="store_true", help="show the backend's own output")
    parser.add_argument("--version", action="version", version="%s %s" % (PROG, __version__))
    return parser


def _read_text(args):
    if args.file:
        if args.file == "-":
            return sys.stdin.read()
        path = os.path.expanduser(args.file)
        if not os.path.exists(path):
            raise TTSError("input file not found: %s" % path)
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    if args.text:
        return " ".join(args.text)
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise TTSError("no text given; pass it as an argument, with --file, or on stdin")


def _apply_overrides(provider, args):
    for item in args.overrides:
        if "=" not in item:
            raise TTSError("expected KEY=VALUE, got %r" % item)
        key, raw = item.split("=", 1)
        key = key.strip()
        # Accept both "threads=8" and "llamacpp.threads=8".
        if "." in key:
            target, key = key.split(".", 1)
            if target != provider.name:
                raise TTSError("--set %s.%s does not apply to provider %r" % (target, key, provider.name))
        defaults = config.DEFAULTS["providers"][provider.name]
        if key not in defaults:
            raise TTSError(
                "unknown setting %r for %s (valid: %s)" % (key, provider.name, ", ".join(sorted(defaults)))
            )
        provider.settings[key] = config.coerce(raw, defaults[key])

    if args.model:
        provider.settings["model"] = args.model


def speak(argv):
    args = _speak_parser().parse_args(argv)
    cfg = config.load()

    text = _read_text(args).strip()
    if not text:
        raise TTSError("nothing to speak: the input was empty")

    provider = providers.build(args.provider or cfg["provider"], cfg, verbose=args.verbose)
    _apply_overrides(provider, args)

    if args.output:
        out_path = os.path.expanduser(args.output)
        parent = os.path.dirname(os.path.abspath(out_path))
        if not os.path.isdir(parent):
            raise TTSError("output directory does not exist: %s" % parent)
        suffix = os.path.splitext(out_path)[1].lstrip(".").lower()
        if suffix and provider.name != "openai" and suffix != provider.default_format:
            raise TTSError(
                "%s can only write .%s files (got %s)" % (provider.name, provider.default_format, args.output)
            )
        temporary = False
    else:
        handle, out_path = tempfile.mkstemp(prefix="local-tts-", suffix="." + provider.default_format)
        os.close(handle)
        temporary = True

    if args.dry_run:
        if not hasattr(provider, "build_command"):
            print("%s: no external command (this backend speaks HTTP)" % provider.name)
        else:
            builder = provider.build_command
            cmd = builder(text, out_path, args.voice) if provider.name == "llamacpp" else builder(text, out_path)
            print(" ".join(cmd))
        if temporary:
            os.unlink(out_path)
        return 0

    try:
        provider.synthesize(text, out_path, voice=args.voice)

        should_play = not args.no_play and (args.play or (temporary and cfg["play"]))
        played = False
        if should_play:
            played = audio.play(out_path, args.player or cfg["player"], verbose=args.verbose)
            if not played:
                print(
                    "no audio player found (tried: %s). Install ffmpeg, or use --output."
                    % ", ".join(name for name, _ in audio.PLAYERS),
                    file=sys.stderr,
                )

        if not temporary:
            print(out_path)
        elif args.keep or not played:
            print(out_path)
            temporary = False
    finally:
        if temporary and os.path.exists(out_path):
            os.unlink(out_path)
    return 0


def list_providers(argv):
    parser = argparse.ArgumentParser(prog="%s providers" % PROG, description="List the available backends.")
    parser.parse_args(argv)
    cfg = config.load()
    for name in providers.names():
        marker = "*" if name == cfg["provider"] else " "
        print("%s %-9s %s" % (marker, name, providers.DESCRIPTIONS.get(name, "")))
    print("\n* = current default (change with `%s config --set provider=<name>`)" % PROG)
    return 0


def check(argv):
    parser = argparse.ArgumentParser(prog="%s check" % PROG, description="Verify backends and audio players.")
    parser.parse_args(argv)
    cfg = config.load()

    print("config file : %s%s" % (config.config_path(), "" if config.config_path().exists() else " (not created yet)"))
    print("default     : %s" % cfg["provider"])
    print("")
    ok_default = True
    for name in providers.names():
        provider = providers.build(name, cfg)
        ok, message = provider.check()
        print("[%s] %-9s %s" % ("ok" if ok else "--", name, message))
        if name == cfg["provider"]:
            ok_default = ok
    print("")
    found = audio.available_players()
    print("players     : %s" % (", ".join(found) if found else "none found (install ffmpeg for ffplay)"))
    return 0 if ok_default else 1


def config_command(argv):
    parser = argparse.ArgumentParser(prog="%s config" % PROG, description="Inspect or change the configuration.")
    parser.add_argument("--show", action="store_true", help="print the effective configuration")
    parser.add_argument("--path", action="store_true", help="print the config file path")
    parser.add_argument("--init", action="store_true", help="write a config file containing the defaults")
    parser.add_argument("--set", dest="assignments", action="append", default=[], metavar="KEY=VALUE",
                        help="set provider, play, player, or <provider>.<key> (repeatable)")
    args = parser.parse_args(argv)

    if args.path:
        print(config.config_path())
        return 0

    if args.init:
        path = config.config_path()
        if path.exists():
            raise TTSError("%s already exists" % path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config.DEFAULTS, indent=2) + "\n", encoding="utf-8")
        print("wrote %s" % path)
        return 0

    if args.assignments:
        config.set_values(args.assignments)
        print("updated %s" % config.config_path())
        if not args.show:
            return 0

    print(json.dumps(config.load(), indent=2))
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv and argv[0] in SUBCOMMANDS:
            handler = {"config": config_command, "providers": list_providers, "check": check}[argv[0]]
            return handler(argv[1:])
        return speak(argv)
    except TTSError as exc:
        print("%s: error: %s" % (PROG, exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
