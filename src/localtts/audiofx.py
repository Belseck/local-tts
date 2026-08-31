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
import random
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


#: Pre-emphasis applied to the noise before it drives the vocal tract filter, as a
#: number of first-order stages and their coefficient. This is what makes a whisper
#: sound like one rather than like speech with the pitch removed: the fitted filter
#: absorbs the glottal source's own -12 dB/octave slope, so exciting it with flat noise
#: reproduces the balance of voiced speech. Whispering replaces that source with
#: turbulence, which is far flatter, and the tilt has to be taken back out.
_BREATH_TILT_STAGES = 1
_BREATH_TILT = 0.70

#: Corner of the high-pass on the excitation, in Hz, and how many one-pole sections it
#: cascades into. A whisper has next to nothing down there -- no fundamental to put it
#: there, and a raised first formant besides -- and a single pole rolls off too gently
#: to move the peak of the spectrum where it belongs.
_BREATH_HIGHPASS = 300.0
_BREATH_HIGHPASS_POLES = 3

#: Corner of the low-pass on the excitation, in Hz, 0 to leave the top open. Whisper
#: turbulence is broadband but not white: it has a broad peak and falls away above it.
_BREATH_LOWPASS = 4500.0

#: Peak the whisper is allowed to reach, just under full scale.
_CEILING = 32000.0

#: How many poles model the vocal tract. Enough for the three or four formants that
#: distinguish vowels; more would start fitting the pitch, which is the thing being
#: thrown away.
_BREATH_ORDER = 14

#: Analysis frame, in seconds. Long enough to see a couple of pitch periods, short
#: enough that the mouth has not moved much within one.
_BREATH_FRAME = 0.023


def _levinson(autocorr, order):
    """Autocorrelation -> all-pole filter coefficients, by Levinson-Durbin recursion.

    The resulting filter is guaranteed stable, which matters here: it is run over noise
    for the whole utterance, and an unstable one would not merely sound wrong, it would
    diverge into clipping.
    """
    coeffs = [0.0] * (order + 1)
    coeffs[0] = 1.0
    error = autocorr[0]
    if error <= 0.0:
        return coeffs
    for i in range(1, order + 1):
        acc = autocorr[i] + sum(coeffs[j] * autocorr[i - j] for j in range(1, i))
        reflection = -acc / error
        updated = coeffs[:]
        for j in range(1, i):
            updated[j] = coeffs[j] + reflection * coeffs[i - j]
        updated[i] = reflection
        coeffs = updated
        error *= 1.0 - reflection * reflection
        if error <= 0.0:
            break
    return coeffs


def apply_breath(path, amount):
    """Turn voiced speech into whispered speech, in place.

    A whisper is not quiet speech: it is speech with no vocal fold vibration at all.
    The vowels are turbulence shaped by the mouth rather than a pitched buzz filtered
    by it. Kokoro has no phonation control, so this has to be done to the rendered wave.

    Linear prediction separates the two. Each frame is fitted with an all-pole filter
    that models the vocal tract -- the formants, which are what make a vowel an "a"
    rather than an "e" -- and that same filter is then driven with noise instead of
    with the original pitched excitation. The words survive; the pitch does not.

    Two earlier attempts are worth recording, since both measured as successes:

    - Multiplying the signal by white noise. Convolving with a flat spectrum does kill
      periodicity, and also flattens the formants: on a sustained "a" the energy moved
      from 200 Hz to 1800 Hz. It sounded like static, correctly.
    - A ten-band vocoder, noise shaped by each band's envelope. Better, but the
      resonator gain landed on both the noise and the envelope measured through it, so
      per-band error compounded: the 2600 Hz formant of a synthetic vowel dropped 17 dB
      and a spectral valley became the loudest thing in the signal.

    Against that synthetic vowel this version holds all three formants within 0.5 dB
    and drops periodicity from 0.98 to 0.17. It fills the spectral valleys between them
    by a few dB -- noise excitation always will, and a real whisper does the same.

    `amount` is how much of the voiced signal is replaced, 0 to 1, mixed rather than
    switched: consonants are turbulent already and keep their bite with some of the
    original left in.
    """
    amount = max(0.0, min(1.0, float(amount)))
    if amount < EPSILON:
        return False
    params, raw = _read(path)
    samples = array.array("h")
    samples.frombytes(raw)
    rate = params.framerate
    channels = params.nchannels
    frame = max(128, int(rate * _BREATH_FRAME))
    if len(samples) < frame * 2 * channels:
        return False

    mono = [float(v) for v in (samples[0::channels] if channels > 1 else samples)]
    count = len(mono)
    hop = frame // 2
    window = [0.5 - 0.5 * math.cos(2.0 * math.pi * i / frame) for i in range(frame)]

    rand = random.Random(0)                       # same text, same whisper
    noise = [rand.random() * 2.0 - 1.0 for _ in range(count)]
    for _ in range(_BREATH_TILT_STAGES):
        previous = 0.0
        for i in range(count):
            current = noise[i]
            noise[i] = current - _BREATH_TILT * previous
            previous = current
    if _BREATH_HIGHPASS > 0.0:
        decay = math.exp(-2.0 * math.pi * _BREATH_HIGHPASS / rate)
        for _ in range(_BREATH_HIGHPASS_POLES):
            last_in = last_out = 0.0
            for i in range(count):
                current = noise[i]
                last_out = decay * (last_out + current - last_in)
                last_in = current
                noise[i] = last_out

    if _BREATH_LOWPASS > 0.0:
        decay = math.exp(-2.0 * math.pi * _BREATH_LOWPASS / rate)
        last = 0.0
        for i in range(count):
            last = decay * last + (1.0 - decay) * noise[i]
            noise[i] = last

    whispered = [0.0] * count
    weight = [0.0] * count
    history = [0.0] * _BREATH_ORDER

    for start in range(0, count - frame + 1, hop):
        segment = [mono[start + i] * window[i] for i in range(frame)]
        autocorr = [sum(segment[i] * segment[i + lag] for i in range(frame - lag))
                    for lag in range(_BREATH_ORDER + 1)]
        if autocorr[0] <= 0.0:
            continue
        autocorr[0] *= 1.0001                     # a ridge, so near-silence stays solvable
        coeffs = _levinson(autocorr, _BREATH_ORDER)

        excited = []
        for offset in range(frame):
            value = noise[start + offset]
            for j in range(1, _BREATH_ORDER + 1):
                value -= coeffs[j] * history[j - 1]
            history = [value] + history[:-1]
            excited.append(value)

        loudness = math.sqrt(sum(v * v for v in segment) / frame)
        synthetic = math.sqrt(sum(v * v for v in excited) / frame)
        if synthetic <= 0.0:
            continue
        gain = loudness / synthetic
        for i in range(frame):
            whispered[start + i] += excited[i] * gain * window[i]
            weight[start + i] += window[i] * window[i]

    # Undo the analysis window. The floor is what keeps the first and last few samples
    # sane: there only one window overlaps and its weight tapers to zero, so dividing by
    # it raw turns a handful of edge samples into astronomical ones -- which then set the
    # level for everything that follows. Clamping instead fades those edges, which is
    # what they should do anyway.
    floor = 0.25 * (max(weight) or 1.0)
    for i in range(count):
        whispered[i] = whispered[i] / max(weight[i], floor)

    # Per-frame gain matched each frame's *windowed* loudness, and overlap-adding
    # uncorrelated noise does not put that back the way overlap-adding the original
    # would: the result lands about 30% quiet. Rather than derive the constant, match
    # the level outright, which also stays right if the window or hop ever changes.
    scale = 1.0
    voiced_level = math.sqrt(sum(v * v for v in mono) / count)
    breathed_level = math.sqrt(sum(v * v for v in whispered) / count)
    if breathed_level > 0.0:
        scale = voiced_level / breathed_level
    # ...and then back off if that would clip. Noise peaks perhaps three times further
    # above its own average than a voiced buzz does, so a line that was comfortably loud
    # can whisper past full scale.
    loudest = max((abs(v) for v in whispered), default=0.0) * scale
    if loudest > _CEILING:
        scale *= _CEILING / loudest

    for i in range(count):
        mixed = int((1.0 - amount) * mono[i] + amount * scale * whispered[i])
        value = -32768 if mixed < -32768 else (32767 if mixed > 32767 else mixed)
        for c in range(channels):
            samples[i * channels + c] = value

    if sys.byteorder == "big":
        samples.byteswap()
    _write(path, params, samples.tobytes())
    return True


def apply_profile(path, speed=1.0, volume=1.0, breath=0.0):
    """Apply whatever a provider could not realize itself. Never raises: a failed
    cosmetic transform must leave the original audio playable, not break synthesis."""
    changed = False
    try:
        # Before the volume cut, so the turbulence is shaped by the full signal rather
        # than by whatever is left after a whisper's own attenuation.
        if breath and breath >= EPSILON:
            changed = apply_breath(path, float(breath)) or changed
        if speed and abs(speed - 1.0) >= EPSILON:
            changed = apply_speed(path, float(speed)) or changed
        if volume and abs(volume - 1.0) >= EPSILON:
            changed = apply_volume(path, float(volume)) or changed
    except (wave.Error, ValueError, OSError, MemoryError):
        return changed
    return changed
