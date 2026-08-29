"""Turning documents into speakable text: markdown stripping and chunking."""

import concurrent.futures
import contextlib
import os
import re
import tempfile
import threading

from localtts.errors import TTSError

MARKDOWN_SUFFIXES = (".md", ".markdown", ".mdown", ".mkd", ".mdx")

_RULES = (
    (re.compile(r"^```.*?^```", re.M | re.S), " "),          # fenced code
    (re.compile(r"^~~~.*?^~~~", re.M | re.S), " "),
    (re.compile(r"`+([^`\n]+)`+"), r"\1"),                    # inline code
    (re.compile(r"!\[([^\]]*)\]\([^)]*\)"), r"\1"),           # images
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),            # inline links
    (re.compile(r"\[([^\]]+)\]\[[^\]]*\]"), r"\1"),           # reference links
    (re.compile(r"^\s*\[[^\]]+\]:\s*\S+.*$", re.M), ""),      # link definitions
    (re.compile(r"<[^>\s][^>]*>"), " "),                      # raw html
    (re.compile(r"^\s{0,3}#{1,6}\s*(.*?)\s*#*\s*$", re.M), r"\1."),   # headings
    (re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$", re.M), ""),      # rules
    (re.compile(r"^\s{0,3}>+\s?", re.M), ""),                 # blockquotes
    (re.compile(r"^\s*[-*+]\s+", re.M), ""),                  # bullets
    (re.compile(r"^\s*\d+[.)]\s+", re.M), ""),                # numbered items
    (re.compile(r"~~(\S.*?\S)~~"), r"\1"),                    # strikethrough
    (re.compile(r"(\*\*\*|___)(\S.*?\S)\1"), r"\2"),          # bold italic
    (re.compile(r"(\*\*|__)(\S.*?\S)\1"), r"\2"),             # bold
    (re.compile(r"(?<![*\w])\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\w)"), r"\1"),  # italic
    (re.compile(r"^\s*\|?[\s:|-]{6,}\|?\s*$", re.M), ""),     # table separators
    (re.compile(r"[ \t]*\|[ \t]*"), ", "),                    # table cells
)


def strip_markdown(raw):
    """Reduce markdown to the words a narrator should actually say."""
    text = raw.replace("\r\n", "\n")
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    text = re.sub(r"^[ \t]*,\s*|,\s*$", "", text, flags=re.M)   # table row edges
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n\n".join(part.strip() for part in text.split("\n\n") if part.strip())


def looks_like_markdown(path):
    return str(path).lower().endswith(MARKDOWN_SUFFIXES)


def _split_oversized(sentence, limit):
    """Break one long sentence at clause boundaries, then mid-word as a last resort."""
    parts = [sentence]
    while max(len(p.split()) for p in parts) > limit:
        index = max(range(len(parts)), key=lambda i: len(parts[i].split()))
        longest = parts[index]
        pieces = re.split(r"(?<=[:;,])\s+", longest, maxsplit=1)
        if len(pieces) == 1:
            words = longest.split()
            middle = len(words) // 2
            pieces = [" ".join(words[:middle]), " ".join(words[middle:])]
        parts[index:index + 1] = [p for p in pieces if p.strip()]
    return parts


def chunks(text, limit):
    """Split text into pieces of at most `limit` words, never mid-sentence if avoidable."""
    if not limit or limit <= 0:
        return [text]

    pieces = []
    for paragraph in [p for p in text.split("\n\n") if p.strip()]:
        batch, count = [], 0
        for sentence in re.split(r"(?<=[.!?])\s+", " ".join(paragraph.split())):
            if not sentence:
                continue
            for part in _split_oversized(sentence, limit):
                words = len(part.split())
                if batch and count + words > limit:
                    pieces.append(" ".join(batch))
                    batch, count = [], 0
                batch.append(part)
                count += words
        if batch:
            pieces.append(" ".join(batch))
    return pieces or [text]


#: <name>...</name> tone tags. \<, \>, \\ escape a literal angle bracket or backslash --
#: for text that has to say "<anger>" out loud instead of meaning the tag.
_TAG_TOKEN = re.compile(r"\\(?P<esc>[<>\\])|</(?P<close>[a-zA-Z][\w-]*)>|<(?P<open>[a-zA-Z][\w-]*)>")


class ToneTagError(TTSError):
    """Malformed <tag>...</tag> markup: unclosed, or mismatched open/close."""


def _tag_tokens(text):
    """Tokenize into ("text", str) / ("open", name) / ("close", name), unescaping \\<, \\>,
    \\\\ back to a literal character as plain "text" tokens along the way."""
    pos = 0
    for match in _TAG_TOKEN.finditer(text):
        if match.start() > pos:
            yield "text", text[pos:match.start()]
        if match.group("esc"):
            yield "text", match.group("esc")
        elif match.group("open"):
            yield "open", match.group("open")
        else:
            yield "close", match.group("close")
        pos = match.end()
    if pos < len(text):
        yield "text", text[pos:]


def _tagged_spans(text):
    """(chunk, active_tag_names) pairs covering `text` in order, where active_tag_names is
    the tuple of currently-open tags (outer-first) for that chunk -- () for plain text.
    Nesting is allowed: text inside <a><b>...</b></a> gets ("a", "b"). Raises
    ToneTagError on an unclosed tag or a </x> that doesn't match the innermost open tag --
    almost certainly a typo, so this fails loudly rather than guessing what was meant.
    """
    stack = []
    spans = []
    buf = []

    def flush():
        chunk = "".join(buf)
        if chunk:
            spans.append((chunk, tuple(stack)))
        buf.clear()

    for kind, value in _tag_tokens(text):
        if kind == "text":
            buf.append(value)
        elif kind == "open":
            flush()
            stack.append(value)
        else:
            if not stack or stack[-1].lower() != value.lower():
                raise ToneTagError(
                    "</%s> does not close the currently open tag (%s)"
                    % (value, ("<%s>" % stack[-1]) if stack else "nothing is open")
                )
            flush()
            stack.pop()
    flush()
    if stack:
        raise ToneTagError("<%s> was never closed" % stack[-1])
    return spans


#: Built-in <tag> vocabulary: name -> (instructions phrase for a backend with a free-text
#: style hook, speed multiplier, volume multiplier). Speed/volume are a *prosody
#: approximation* -- real for any backend that exposes a genuine rate/volume knob (piper's
#: --length-scale/--volume, kokoro's speed), not "true" emotional synthesis the way a
#: model that actually reads the word "anger" and reacts to it is. The numbers are
#: deliberately modest (not "1.5x volume, 3x speed") so a chain of them stays listenable;
#: treat them as a reasonable default preset, not a measured fact -- override via a
#: provider's own `speed`/`length_scale`/`volume` setting if you want something different,
#: or per-tag if that's ever asked for.
TAG_PROFILES = {
    "anger":       ("Speak with a sharp, angry edge.", 1.10, 1.15),
    "angry":       ("Speak with a sharp, angry edge.", 1.10, 1.15),
    "happy":       ("Speak with a warm smile in your voice.", 1.05, 1.05),
    "joy":         ("Speak with bright, joyful energy.", 1.08, 1.08),
    "sad":         ("Speak slowly, in a sad, downcast tone.", 0.90, 0.90),
    "sadness":     ("Speak slowly, in a sad, downcast tone.", 0.90, 0.90),
    "fear":        ("Speak nervously, as if afraid.", 1.10, 0.90),
    "afraid":      ("Speak nervously, as if afraid.", 1.10, 0.90),
    "surprise":    ("Speak with sudden, surprised emphasis.", 1.05, 1.10),
    "surprised":   ("Speak with sudden, surprised emphasis.", 1.05, 1.10),
    "disgust":     ("Speak with a tone of disgust or distaste.", 0.95, 1.00),
    "calm":        ("Speak calmly and evenly.", 0.95, 0.95),
    "excited":     ("Speak with excited, energetic emphasis.", 1.12, 1.10),
    "serious":     ("Speak in a serious, measured tone.", 0.95, 1.00),
    "whisper":     ("Whisper, very quietly and softly.", 0.90, 0.55),
    "sarcastic":   ("Speak with a dry, sarcastic edge.", 1.00, 1.00),
    "sarcasm":     ("Speak with a dry, sarcastic edge.", 1.00, 1.00),
    "urgent":      ("Speak urgently, with a sense of importance.", 1.15, 1.10),
    "gentle":      ("Speak gently and softly.", 0.90, 0.85),
    "confident":   ("Speak confidently and assertively.", 1.00, 1.05),
    "tired":       ("Speak slowly, as if tired.", 0.85, 0.85),
    "playful":     ("Speak in a light, playful tone.", 1.05, 1.00),
    # These three also drive auto_tone (see resolve_tone_segments()) -- an explicit
    # <question> tag and an auto-detected question sentence read the same way.
    "question":    ("Speak with a curious, questioning lift at the end.", 1.00, 1.00),
    "exclamation": ("Speak with excited, energetic emphasis.", 1.10, 1.10),
    "assertion":   (None, 1.00, 1.00),
}


def tag_profile(name):
    """One tag's {"instructions", "speed", "volume"} -- a built-in preset from
    TAG_PROFILES when the name matches one (case-insensitive), else a generic
    instructions-only phrase built from the name itself with speed/volume left at 1.0
    (unchanged): there's no fixed vocabulary to fabricate a prosody preset for an
    arbitrary word an agent invents, but the phrase alone still does something on a
    backend with a real free-text style hook (openai)."""
    key = name.lower()
    if key in TAG_PROFILES:
        instructions, speed, volume = TAG_PROFILES[key]
        return {"instructions": instructions, "speed": speed, "volume": volume}
    return {"instructions": "Speak in a tone that conveys %s." % name, "speed": 1.0, "volume": 1.0}


def _combine_profiles(names):
    """Nested tags (<a><b>...</b></a>) combine: instructions concatenate outer-first,
    speed/volume multiply (so two mild adjustments compound into a stronger one)."""
    phrases, speed, volume = [], 1.0, 1.0
    for name in names:
        profile = tag_profile(name)
        if profile["instructions"]:
            phrases.append(profile["instructions"])
        speed *= profile["speed"]
        volume *= profile["volume"]
    return {"instructions": " ".join(phrases) or None, "speed": speed, "volume": volume}


_NEUTRAL_PROFILE = {"instructions": None, "speed": 1.0, "volume": 1.0}


def resolve_tone_segments(text, auto_tone=False):
    """Split `text` into (chunk, profile) pairs for a backend that can vary tone/emotion
    per call -- profile is None for plain text with no tag and no auto_tone match
    (identical to today's zero-tag behavior), else a dict from _combine_profiles().

    `<name>...</name>` sets the profile for everything inside it; an explicit tag always
    wins over auto-detection for its span. Escape a literal angle bracket with `\\<` /
    `\\>` (and `\\\\` for a literal backslash) if the text genuinely needs to say e.g.
    "<anger>" out loud.

    Where no tag is active and `auto_tone` is true, each *sentence* in that stretch is
    classified by its own trailing punctuation ("question"/"exclamation"/else
    "assertion") and gets that category's built-in profile.

    Adjacent chunks that end up with the identical profile are merged, so plain text with
    no tags and auto_tone off always collapses back to the single (text, None) segment
    that a plain, untagged call has always been.
    """
    expanded = []
    for chunk, active_tags in _tagged_spans(text):
        if active_tags:
            profile = _combine_profiles(active_tags)
            expanded.append((chunk, profile if profile != _NEUTRAL_PROFILE else None))
            continue
        if not auto_tone:
            expanded.append((chunk, None))
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", chunk.strip()):
            if not sentence:
                continue
            if sentence.endswith("?"):
                category = "question"
            elif sentence.endswith("!"):
                category = "exclamation"
            else:
                category = "assertion"
            profile = tag_profile(category)
            expanded.append((sentence, profile if profile != _NEUTRAL_PROFILE else None))

    merged = []
    for chunk, profile in expanded:
        if merged and merged[-1][1] == profile:
            merged[-1][0] += " " + chunk
        else:
            merged.append([chunk, profile])
    return [(chunk.strip(), profile) for chunk, profile in merged if chunk.strip()] or [(text, None)]


def strip_tone_tags(text):
    """Remove <name>...</name> tone tags -- keeping the words inside them, unescaping
    \\<, \\>, \\\\ -- for any backend that doesn't understand them (see tone_segments()),
    so it speaks the sentence instead of literally reading out the markup. Still raises
    ToneTagError on malformed markup: a typo should be reported the same way regardless of
    which provider happens to be configured, not silently swallowed differently per backend.
    """
    chunks = [chunk for chunk, _ in _tagged_spans(text)]
    return re.sub(r"[ \t]+", " ", "".join(chunks)).strip()



@contextlib.contextmanager
def _own_the_stream(provider):
    """Take fragment publishing away from `provider` for the duration of a loop that is
    itself producing the ordered parts, and hand that loop the sink.

    Without this a provider that also emits internally (its own tone segments) would
    publish both its pieces and ours, and the stream would play the same audio twice.
    Whoever owns the outermost loop owns the ordering, so it owns the sink.
    """
    sink = getattr(provider, "on_part", None)
    if sink is None:
        yield None
        return
    provider.on_part = None
    try:
        yield sink
    finally:
        provider.on_part = sink


def _synthesize_with_audiofx(provider, text, out_path, voice, on_progress):
    """Render `text` segment by segment, applying each segment's speed/volume to its own
    wav, and join the result. Returns the output path, or None when this is not worth
    doing -- no tags, or nothing left for audiofx to apply -- so the caller falls back to
    its normal single/chunked path unchanged.

    Only PCM wav can be shaped this way, which is every offline backend that lands here
    (llamacpp, command, and rvc's own composed output); a provider whose default_format
    is compressed opts out rather than being decoded and re-encoded.
    """
    from localtts import audio, audiofx

    if provider.default_format != "wav":
        return None
    if not getattr(provider, "allow_audio_fx", True):
        return None      # the backend's own output is left exactly as it rendered it
    settings = getattr(provider, "settings", None) or {}
    segments = resolve_tone_segments(text, auto_tone=bool(settings.get("auto_tone")))
    if len(segments) == 1 and segments[0][1] is None:
        return None

    does_speed = getattr(provider, "realizes_speed", False)
    does_volume = getattr(provider, "realizes_volume", False)
    pending = [
        (chunk, 1.0 if does_speed or not profile else profile["speed"],
                1.0 if does_volume or not profile else profile["volume"])
        for chunk, profile in segments
    ]
    if all(abs(sp - 1.0) < audiofx.EPSILON and abs(vol - 1.0) < audiofx.EPSILON
           for _, sp, vol in pending):
        return None                      # tags present, but nothing for us to realize

    work = tempfile.mkdtemp(prefix="local-tts-tone-")
    parts = [os.path.join(work, "%04d.wav" % index) for index in range(len(pending))]
    try:
        with _own_the_stream(provider) as emit:
            for index, ((chunk, speed, volume), part) in enumerate(zip(pending, parts)):
                provider.synthesize(strip_tone_tags(chunk), part, voice=voice)
                audiofx.apply_profile(part, speed=speed, volume=volume)
                if emit:
                    emit(part)          # playable now; the rest are still being made
                if on_progress:
                    on_progress(index + 1, len(pending))
        audio.concat_wavs(parts, out_path)
    finally:
        for part in parts:
            if os.path.exists(part):
                os.unlink(part)
        if os.path.isdir(work):
            os.rmdir(work)
    return out_path


def synthesize_chunked(provider, text, out_path, voice=None, on_progress=None):
    """One call for short text; chunk-and-join when the backend needs small prompts.

    Chunks are synthesized concurrently, bounded by provider.max_workers -- each
    subprocess-based call pays its own fixed startup cost (process spawn, model load)
    on top of the actual synthesis time, so overlapping chunks is most of the win for a
    backend that has to chunk. `on_progress(done, total)`, if given, is called after
    each chunk finishes (from whichever worker thread finished it).

    Lives here rather than in cli.py because a provider that composes another provider
    (rvc, converting a base voice) needs the same chunk-and-join behavior for its own
    inner synthesis call, and providers must not import cli.py (cli.py imports
    providers -- that would be circular). Also the one place <tag> tone tags (see
    tone_segments()) get stripped for a provider that can't act on them -- rvc's own
    inner call passes through here too, so a base_provider that doesn't support tags
    (kokoro, piper, ...) never sees the literal brackets, while one that does (openai)
    gets the raw text so it can do its own per-segment synthesis.
    """
    from localtts import audio   # local import: audio.py has no reason to import text.py,
                                  # but keeping the edge one-directional here avoids ever
                                  # having to worry about load order between the two.

    if not getattr(provider, "supports_tone_tags", False):
        # The backend cannot vary tone itself -- but speed and volume are measurable, so
        # render each tagged span separately and shape it afterwards rather than throwing
        # the emotion away with the markup. Only worth the extra calls when a tag actually
        # asks for a change; plain text still takes the single-call path below.
        if not getattr(provider, "handles_tone_segments", False):
            rendered = _synthesize_with_audiofx(provider, text, out_path, voice, on_progress)
            if rendered is not None:
                return rendered
        text = strip_tone_tags(text)

    pieces = chunks(text, provider.max_words)
    if len(pieces) == 1:
        return provider.synthesize(text, out_path, voice=voice)

    work = tempfile.mkdtemp(prefix="local-tts-chunks-")
    parts = [os.path.join(work, "%04d.%s" % (index, provider.default_format))
             for index in range(1, len(pieces) + 1)]
    done = 0
    done_lock = threading.Lock()

    with _own_the_stream(provider) as emit:
        finished, next_to_emit = set(), 0

        def publish(index):
            """Chunks finish out of order but must be *heard* in order, so a chunk is
            published only once every chunk before it has been. Called with done_lock
            held."""
            nonlocal next_to_emit
            finished.add(index)
            while next_to_emit in finished:
                emit(parts[next_to_emit])
                next_to_emit += 1

        def synth_one(index):
            nonlocal done
            provider.synthesize(pieces[index], parts[index], voice=voice)
            with done_lock:
                done += 1
                if emit:
                    publish(index)
                if on_progress:
                    on_progress(done, len(pieces))

        workers = max(1, min(provider.max_workers, len(pieces)))
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        try:
            futures = [pool.submit(synth_one, index) for index in range(len(pieces))]
            for future in concurrent.futures.as_completed(futures):
                future.result()  # re-raises the first chunk failure; others keep running
            audio.concat_wavs(parts, out_path)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
            for part in parts:
                if os.path.exists(part):
                    os.unlink(part)
            if os.path.isdir(work):
                os.rmdir(work)
    return out_path
