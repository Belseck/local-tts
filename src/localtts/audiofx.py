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
overlap-add fallback so the feature never silently does nothing on a machine without
ffmpeg. Both are no-ops for a factor of 1.0, so the untagged path is untouched.
"""

import array
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


def _ola_tempo(path, factor):
    """Overlap-add time stretch: pure stdlib, keeps pitch roughly intact.

    Cross-fades fixed-size output windows taken from input positions advancing at
    `factor` times the output rate. Not as clean as atempo on music, entirely adequate
    for a sentence of speech, and it means the fallback path still changes the timing
    instead of doing nothing.
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
    out = array.array("h", bytes(0))
    fade = [i / float(overlap) for i in range(overlap)]
    position = 0.0
    previous_tail = None
    while int(position) + window < total:
        start = int(position)
        block = samples[start * channels:(start + window) * channels]
        if previous_tail is not None:
            for i in range(overlap):
                for c in range(channels):
                    k = i * channels + c
                    block[k] = int(previous_tail[k] * (1.0 - fade[i]) + block[k] * fade[i])
        out.extend(block[:hop_out * channels])
        previous_tail = block[hop_out * channels:]
        position += hop_in
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
