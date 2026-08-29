"""Post-synthesis speed/volume shaping for backends that cannot do it themselves.

A `<tag>` profile (see text.TAG_PROFILES) asks for two measurable things -- a speed
multiplier and a volume multiplier -- plus a free-text instruction only openai can act
on. Providers realize what they can natively: piper has --length-scale and --volume,
kokoro has -s (speed) but no volume knob at all, llamacpp and command have neither.

Whatever a provider cannot realize is applied here instead, to the rendered wav of that
one segment, so an emotion still *sounds* different on a backend with no tone hook.
This runs per segment rather than over the finished file precisely because the profile
varies by segment -- shaping the join afterwards could not tell them apart.

Deliberately dependency-free: volume is exact integer scaling via `array`, and time
stretching prefers ffmpeg's `atempo` (proper, pitch-preserving) with a pure-Python
WSOLA fallback so the feature never silently does nothing on a machine without ffmpeg. Both are no-ops for a factor of 1.0, so the untagged path is untouched.
"""

import array
import math
import os
import shutil
import subprocess
import sys
import tempfile
import wave

#: Below this, a multiplier is not worth a rewrite of the file.
EPSILON = 0.01
#: atempo accepts 0.5-2.0 per filter; outside that we chain instances.
_ATEMPO_MIN, _ATEMPO_MAX = 0.5, 2.0


def _read(path):
    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("only 16-bit PCM is supported")
        return w.getparams(), w.readframes(w.getnframes())


def _write(path, params, frames):
    with wave.open(path, "wb") as w:
        w.setparams(params)
        w.writeframes(frames)


def apply_volume(path, factor):
    """Scale amplitude in place. Clamps rather than wrapping: int16 overflow is the
    difference between "louder" and a burst of white noise."""
    if abs(factor - 1.0) < EPSILON:
        return False
    params, raw = _read(path)
    samples = array.array("h")
    samples.frombytes(raw)
    for i, value in enumerate(samples):
        scaled = int(value * factor)
        samples[i] = -32768 if scaled < -32768 else (32767 if scaled > 32767 else scaled)
    if sys.byteorder == "big":
        samples.byteswap()
    _write(path, params, samples.tobytes())
    return True


def _ffmpeg_tempo(path, factor):
    exe = shutil.which("ffmpeg")
    if not exe:
        return False
    # atempo is limited to 0.5..2.0, so chain filters for anything beyond that.
    chain, remaining = [], factor
    while remaining > _ATEMPO_MAX:
        chain.append(_ATEMPO_MAX); remaining /= _ATEMPO_MAX
    while remaining < _ATEMPO_MIN:
        chain.append(_ATEMPO_MIN); remaining /= _ATEMPO_MIN
    chain.append(remaining)
    handle, tmp = tempfile.mkstemp(prefix="local-tts-fx-", suffix=".wav")
    os.close(handle)
    try:
        result = subprocess.run(
            [exe, "-y", "-loglevel", "error", "-i", path,
             "-filter:a", ",".join("atempo=%.6f" % c for c in chain), tmp],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if result.returncode != 0 or not os.path.getsize(tmp):
            return False
        shutil.move(tmp, path)
        return True
    except OSError:
        return False
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


#: WSOLA alignment search radius, in seconds. Has to cover at least one pitch period of
#: the lowest voice we care about (~70 Hz -> 14 ms) or the search cannot find the phase
#: match it is looking for.
_SEARCH_SECONDS = 0.015
#: The search runs in two passes: a coarse sweep of the whole radius, then a fine sweep
#: of +/-_COARSE_STRIDE around the winner. Alignment has to be accurate to a few degrees
#: of a pitch period -- at a coarse stride alone a 220 Hz tone loses a fifth of its
#: energy -- but paying that accuracy across the entire radius costs four times as much
#: for the same answer. This is pure Python, and it runs on every tagged span.
_COARSE_STRIDE = 8
#: Correlation is evaluated on every _CORR_STRIDE'th sample of the overlap.
_CORR_STRIDE = 2


def _match_score(samples, channels, start, tail, overlap):
    """Normalized correlation between the input at `start` and what was just emitted.
    Normalized because a plain dot product just picks whichever candidate is loudest,
    which is not the same question as which one lines up."""
    dot = energy = 0.0
    base = start * channels
    for i in range(0, overlap, _CORR_STRIDE):
        a = tail[i * channels]
        b = samples[base + i * channels]
        dot += a * b
        energy += b * b
    return dot / math.sqrt(energy) if energy > 0 else 0.0


def _best_offset(samples, channels, ideal, tail, overlap, limit, radius):
    """The input offset near `ideal` whose leading samples best continue what was just
    emitted -- the "waveform similarity" in WSOLA.

    This is the whole difference between speech and a robot. A blind fixed hop lands at
    an arbitrary point in the pitch period, so each cross-fade sums two copies of the
    same harmonic at a different phase; they partly cancel, and because the error walks
    with every window the cancellation modulates at the hop rate and reads as a buzz.
    Aligning first means the two halves of every cross-fade are already in phase.
    """
    low = max(0, ideal - radius)
    high = min(limit, ideal + radius)
    if high <= low:
        return max(0, min(ideal, limit))

    best_score, best = None, max(low, min(ideal, high))
    for start in range(low, high + 1, _COARSE_STRIDE):
        score = _match_score(samples, channels, start, tail, overlap)
        if best_score is None or score > best_score:
            best_score, best = score, start
    for start in range(max(low, best - _COARSE_STRIDE),
                       min(high, best + _COARSE_STRIDE) + 1):
        score = _match_score(samples, channels, start, tail, overlap)
        if score > best_score:
            best_score, best = score, start
    return best


def _ola_tempo(path, factor):
    """WSOLA time stretch: pure stdlib, keeps pitch intact.

    Emits fixed-size output windows cross-faded into each other, taking each one from
    the input position that both advances at `factor` times the output rate *and* best
    continues the waveform already emitted (see _best_offset). The ideal input position
    still advances by exactly one hop per window regardless of which offset was chosen,
    so alignment never accumulates into timing drift.

    Not quite ffmpeg's atempo, but close enough for a sentence of speech, and it means a
    machine without ffmpeg gets a real, listenable tempo change rather than a buzz.
    """
    params, raw = _read(path)
    channels = params.nchannels
    samples = array.array("h")
    samples.frombytes(raw)
    if sys.byteorder == "big":
        samples.byteswap()
    total = len(samples) // channels
    window = max(64, int(params.framerate * 0.03))        # ~30ms
    overlap = window // 2
    hop_out = window - overlap
    hop_in = hop_out * factor
    radius = max(_COARSE_STRIDE, int(params.framerate * _SEARCH_SECONDS))
    limit = total - window - 1
    if limit <= 0:
        return False
    out = array.array("h", bytes(0))
    fade = [i / float(overlap) for i in range(overlap)]
    ideal = 0.0
    previous_tail = None
    while int(ideal) < limit:
        if previous_tail is None:
            start = int(ideal)
        else:
            start = _best_offset(samples, channels, int(ideal), previous_tail,
                                 overlap, limit, radius)
        block = samples[start * channels:(start + window) * channels]
        if previous_tail is not None:
            for i in range(overlap):
                for c in range(channels):
                    k = i * channels + c
                    block[k] = int(previous_tail[k] * (1.0 - fade[i]) + block[k] * fade[i])
        out.extend(block[:hop_out * channels])
        previous_tail = block[hop_out * channels:]
        ideal += hop_in
    if previous_tail:
        out.extend(previous_tail)
    if not out:
        return False
    if sys.byteorder == "big":
        out.byteswap()
    _write(path, params._replace(nframes=len(out) // channels), out.tobytes())
    return True


def apply_speed(path, factor):
    """Speed up (>1) or slow down (<1) in place, preserving pitch. Returns whether the
    file was rewritten -- False means the factor was a no-op or nothing could do it."""
    if abs(factor - 1.0) < EPSILON or factor <= 0:
        return False
    if _ffmpeg_tempo(path, factor):
        return True
    try:
        return _ola_tempo(path, factor)
    except (wave.Error, ValueError, OSError, MemoryError):
        return False


def trim_silence(path, keep_seconds=0.01):
    """Trim near-silence from both ends, in place, leaving `keep_seconds` of margin.

    Every fragment arrives with its own lead-in and tail -- a synthesizer's own padding,
    plus whatever conversion adds at the edges. Joined, eight fragments carry eight lots
    of it, which both stretches the utterance and makes the configured gap between
    fragments meaningless: the real gap is that dead air plus the pause. Trimming first
    is what puts `pause_ms` back in charge of the rhythm.
    """
    params, raw = _read(path)
    samples = array.array("h")
    samples.frombytes(raw)
    if sys.byteorder == "big":
        samples.byteswap()
    channels = params.nchannels
    total = len(samples) // channels
    if total < 2:
        return False
    peak = max((abs(v) for v in samples), default=0)
    if not peak:
        return False
    # Relative to this fragment's own peak: an absolute threshold would clip a quiet
    # <whisper> span entirely while doing nothing for a loud one.
    threshold = max(96, int(peak * 0.02))

    def loud(frame):
        base = frame * channels
        return any(abs(samples[base + c]) >= threshold for c in range(channels))

    first = 0
    while first < total and not loud(first):
        first += 1
    if first >= total:
        return False                       # nothing but silence: leave it alone
    last = total - 1
    while last > first and not loud(last):
        last -= 1

    margin = max(0, int(params.framerate * keep_seconds))
    first = max(0, first - margin)
    last = min(total - 1, last + margin)
    if first == 0 and last == total - 1:
        return False
    trimmed = samples[first * channels:(last + 1) * channels]
    if sys.byteorder == "big":
        trimmed.byteswap()
    _write(path, params._replace(nframes=len(trimmed) // channels), trimmed.tobytes())
    return True


def append_silence(path, seconds):
    """Pad a wav with trailing silence, in place. Returns whether anything was written.

    Used for the gap between spoken fragments. Padding the fragment itself, rather than
    inserting silence while joining, is what keeps streamed playback and the saved file
    identical: the stream plays each fragment on its own and never sees a join.
    """
    if not seconds or seconds <= 0:
        return False
    params, raw = _read(path)
    frames = int(params.framerate * float(seconds))
    if frames <= 0:
        return False
    _write(path, params._replace(nframes=params.nframes + frames),
           raw + b"\x00" * (frames * params.sampwidth * params.nchannels))
    return True


def apply_profile(path, speed=1.0, volume=1.0):
    """Apply whatever a provider could not realize itself. Never raises: a failed
    cosmetic transform must leave the original audio playable, not break synthesis."""
    changed = False
    try:
        if speed and abs(speed - 1.0) >= EPSILON:
            changed = apply_speed(path, float(speed)) or changed
        if volume and abs(volume - 1.0) >= EPSILON:
            changed = apply_volume(path, float(volume)) or changed
    except (wave.Error, ValueError, OSError, MemoryError):
        return changed
    return changed
