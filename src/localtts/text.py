"""Turning documents into speakable text: markdown stripping and chunking."""

import concurrent.futures
import os
import re
import tempfile
import threading

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
    providers -- that would be circular).
    """
    from localtts import audio   # local import: audio.py has no reason to import text.py,
                                  # but keeping the edge one-directional here avoids ever
                                  # having to worry about load order between the two.

    pieces = chunks(text, provider.max_words)
    if len(pieces) == 1:
        return provider.synthesize(text, out_path, voice=voice)

    work = tempfile.mkdtemp(prefix="local-tts-chunks-")
    parts = [os.path.join(work, "%04d.%s" % (index, provider.default_format))
             for index in range(1, len(pieces) + 1)]
    done = 0
    done_lock = threading.Lock()

    def synth_one(index):
        nonlocal done
        provider.synthesize(pieces[index], parts[index], voice=voice)
        with done_lock:
            done += 1
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
