#!/usr/bin/env python3
"""Fit the whisper effect's excitation shape against real whispered recordings.

`<whisper>` cannot be synthesized by kokoro, which has no phonation control, so
`audiofx.apply_breath` builds one: it fits each frame with an all-pole filter standing
in for the vocal tract and drives that filter with noise instead of the original pitched
excitation. How the noise is shaped before it gets there decides whether the result
sounds like a whisper or like speech with the pitch removed -- and those constants are
what this script fits.

    # how close is today's whisper to real ones?
    python tools/train_whisper.py --refs whisper-*.wav

    # search for better constants and print them ready to paste
    python tools/train_whisper.py --refs whisper-*.wav --search

Two numbers are reported, and they answer different questions:

  spectral error   Mean distance, in dB, between the long-term spectrum of a whisper
                   we render and that of the references. This is the one the search
                   minimizes, and the one that catches "it kept the voice's timbre".

  formant margin   How far each formant of a synthetic vowel still stands above the
                   valleys beside it, after the effect. This is the guard rail: the
                   spectral error alone will happily trade away the first formant --
                   which carries vowel identity -- to match a reference's overall
                   balance. A setting that wins on error and loses here is a worse
                   whisper, not a better one, whatever the number says.

References should be whispered speech, ideally a few speakers. Anything mono and PCM
will do; convert with `ffmpeg -i in.mp3 -ac 1 -ar 24000 out.wav`. Passages where the
speaker drops back into an ordinary voice are detected and skipped -- they would pull
the fit straight back toward the thing being trained away.
"""
import argparse
import array
import glob
import math
import os
import sys
import tempfile
import wave

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from localtts import audiofx                                     # noqa: E402

#: Band edges for the long-term spectrum, in Hz. Wide and few: the point is the overall
#: balance, and a fine grid would start fitting one microphone's colour.
BANDS = (0, 200, 400, 700, 1100, 1700, 2600, 4000, 6000, 9000, 12000)

#: Above this much periodicity a passage is voiced, not whispered, and is left out.
#: Calibrated against known material with the estimator below: a pure whispered take
#: reads 0.18, one of our own whispers 0.24, and ordinary synthesized speech 0.90. The
#: gap is wide, so the exact cut matters little -- but it has to be measured, not
#: guessed, because a normalized autocorrelation and a plain one land on different
#: scales and a threshold picked for one is meaningless for the other.
VOICED = 0.7


def read_wav(path):
    with wave.open(path, "rb") as handle:
        data = array.array("h")
        data.frombytes(handle.readframes(handle.getnframes()))
        channels, rate = handle.getnchannels(), handle.getframerate()
    if channels > 1:
        data = array.array("h", [int(sum(data[i:i + channels]) / channels)
                                 for i in range(0, len(data) - channels + 1, channels)])
    return [float(v) for v in data], rate


def periodicity(samples, rate, width=2048, probes=7):
    """Peak autocorrelation over human pitch lags: ~1 when voiced, ~0 when not.

    Read at several points and taken as the median. One probe lands wherever it lands --
    on a pause, on a plosive -- and a single reading decides whether a whole passage
    counts as whispered, which is too much weight for one guess.
    """
    if len(samples) < width * 2:
        return 0.0
    span = len(samples) - width * 2
    scores = []
    for probe in range(probes):
        start = int(span * (probe + 0.5) / probes)
        chunk = samples[start:start + width * 2]
        # Mean-removed: rumble and any DC offset the recording carries are perfectly
        # correlated with themselves and would read as a voice that is not there.
        middle = sum(chunk) / len(chunk)
        chunk = [v - middle for v in chunk]
        head = sum(v * v for v in chunk[:width])
        if head <= 0.0:
            continue
        best = 0.0
        for lag in range(int(rate / 400), int(rate / 70)):
            cross = sum(chunk[i] * chunk[i + lag] for i in range(width))
            tail = sum(chunk[i + lag] ** 2 for i in range(width)) or 1.0
            best = max(best, cross / math.sqrt(head * tail))
        scores.append(best)
    if not scores:
        return 0.0
    scores.sort()
    return scores[len(scores) // 2]


def loud_frames(samples, count=24, width=1024, keep=0.4):
    """The loudest `keep` of `count` evenly spaced frames.

    Silence has a spectrum too, and averaging it in would describe the room rather than
    the speech. Recordings differ in how much of them is pause, so a fixed threshold
    would weigh two references differently; a fraction of the frames does not.
    """
    if len(samples) < width * 2:
        return []
    span = len(samples) - width
    frames = []
    for index in range(count):
        start = int(span * (index + 0.5) / count)
        chunk = samples[start:start + width]
        frames.append((sum(v * v for v in chunk), chunk))
    frames.sort(key=lambda pair: pair[0], reverse=True)
    return [chunk for energy, chunk in frames[:max(1, int(count * keep))] if energy > 0.0]


def band_power(frames, rate, low, high, tones=5, width=1024):
    """Mean power in a band, by correlating against sinusoids across it.

    Averaged over several frequencies and several frames on purpose: one frequency in
    one window estimates a single bin, whose variance over noise is as large as its
    mean, so the naive version reports the estimator and not the signal.
    """
    if high <= low or not frames:
        return 1e-12
    total, taken = 0.0, 0
    for chunk in frames:
        for t_index in range(tones):
            freq = low + (high - low) * (t_index + 0.5) / tones
            real = sum(v * math.cos(2 * math.pi * freq * i / rate)
                       for i, v in enumerate(chunk))
            imag = sum(v * math.sin(2 * math.pi * freq * i / rate)
                       for i, v in enumerate(chunk))
            total += (real * real + imag * imag) / (width * width)
            taken += 1
    return total / taken if taken else 1e-12


def centroid(samples, rate, count=24):
    """Where the spectrum's centre of mass sits, in Hz -- how bright the signal is.

    Reported alongside the band error because the two fail differently. The band error
    depends on how frames were chosen and how they were pooled, so two reasonable
    implementations disagree by a decibel or so; the centroid is one number off the same
    frames and barely moves. When a setting wins on error while pushing the centroid an
    octave past the references, believe the centroid: it is measuring the thing a
    listener would call brightness.
    """
    frames = loud_frames(samples, count=count)
    weighted = total = 0.0
    for step in range(1, 60):
        freq = step * 200.0
        if freq >= rate / 2:
            break
        power = band_power(frames, rate, freq - 100.0, freq + 100.0, tones=3)
        magnitude = math.sqrt(power)
        weighted += magnitude * freq
        total += magnitude
    return weighted / total if total else 0.0


def spectrum(samples, rate, count=24):
    """Long-term spectrum in dB per band, normalized to its own loudest band."""
    frames = loud_frames(samples, count=count)
    powers = [band_power(frames, rate, low, min(high, rate / 2 - 1))
              for low, high in zip(BANDS[:-1], BANDS[1:])]
    loudest = max(powers) or 1e-12
    return [10 * math.log10(max(p / loudest, 1e-9)) for p in powers]


def utterances(samples, rate, floor_db=-28.0, shortest_ms=180, pad_ms=40):
    """Cut a recording into the stretches where somebody is speaking.

    Compared one at a time rather than pooled, because pooling averages away exactly
    what is being measured: a speaker whispers the same word louder and thinner and
    faster from one take to the next, and the spread between those takes is the yardstick
    for how close a synthetic one can reasonably get.
    """
    window = int(rate * 0.02)
    hop = window // 2
    count = (len(samples) - window) // hop
    if count < 1:
        return [samples]
    levels = [math.sqrt(sum(v * v for v in samples[i * hop:i * hop + window]) / window)
              for i in range(count)]
    loudest = max(levels) or 1.0
    speaking = [20 * math.log10(level / loudest + 1e-9) > floor_db for level in levels]
    spans, start = [], None
    for index, active in enumerate(speaking):
        if active and start is None:
            start = index
        elif not active and start is not None:
            spans.append((start, index))
            start = None
    if start is not None:
        spans.append((start, len(speaking)))
    pad = int(pad_ms / 1000.0 * rate)
    out = []
    for first, last in spans:
        begin = max(0, first * hop - pad)
        end = min(len(samples), last * hop + window + pad)
        if (end - begin) * 1000.0 / rate >= shortest_ms:
            out.append(samples[begin:end])
    return out or [samples]


def whispered_only(samples, rate):
    """The utterances that are actually whispered, one by one."""
    return [piece for piece in utterances(samples, rate)
            if periodicity(piece, rate) < VOICED]


def reference_profiles(paths):
    """One spectrum per whispered utterance, plus the pooled centroid."""
    profiles, pooled, rate = [], [], None
    for path in paths:
        samples, this_rate = read_wav(path)
        if rate is None:
            rate = this_rate
        elif this_rate != rate:
            raise SystemExit("references must share a sample rate: %s is %d, not %d"
                             % (path, this_rate, rate))
        kept = whispered_only(samples, rate)
        print("  %-28s %5.1fs, %d whispered utterance%s"
              % (os.path.basename(path), len(samples) / rate, len(kept),
                 "" if len(kept) == 1 else "s"))
        for piece in kept:
            profiles.append(spectrum(piece, rate))
            pooled.extend(piece)
    if not profiles:
        raise SystemExit("no whispered speech found; is VOICED calibrated for these?")
    return profiles, centroid(pooled, rate, count=64), rate


def agreement_floor(profiles):
    """How far the references sit from each other -- where fitting has to stop.

    Below this the numbers stop meaning anything: a synthetic whisper that matches the
    references more closely than they match each other is fitting one speaker's takes,
    not learning what a whisper is. This is the loop's stopping condition, and it is
    measured rather than guessed.
    """
    gaps = [distance(a, b)
            for index, a in enumerate(profiles) for b in profiles[index + 1:]]
    return sum(gaps) / len(gaps) if gaps else 0.0


def synthetic_vowel(path, rate, f0=110, formants=(400, 1800, 2800), seconds=1.5):
    """A vowel with formants over a falling source -- what a whisper acts on."""
    samples = [0.0] * int(rate * seconds)
    for harmonic in range(1, int(rate / 2 / f0)):
        freq = f0 * harmonic
        gain = sum(1.0 / (1.0 + ((freq - c) / 70.0) ** 2) for c in formants) / harmonic
        if gain < 1e-5:
            continue
        for i in range(len(samples)):
            samples[i] += gain * math.sin(2 * math.pi * freq * i / rate)
    loudest = max(abs(v) for v in samples)
    data = array.array("h", [int(8000 * v / loudest) for v in samples])
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(data.tobytes())
    return formants, (1100, 2300, 3700)


#: Where the probe vowel's formants and the valleys between them sit, in Hz.
PROBE_FORMANTS = (400, 1800, 2800)
PROBE_VALLEYS = (1100, 2300, 3700)


def formant_margin(source, path, rate):
    """Smallest margin, in dB, by which a formant still beats the valleys beside it."""
    formants, valleys = PROBE_FORMANTS, PROBE_VALLEYS
    with open(source, "rb") as src, open(path, "wb") as dst:
        dst.write(src.read())
    audiofx.apply_breath(path, 1.0)
    samples, _ = read_wav(path)
    frames = loud_frames(samples)
    peaks = [band_power(frames, rate, hz - 120, hz + 120) for hz in formants]
    floors = [band_power(frames, rate, hz - 120, hz + 120) for hz in valleys]
    worst = None
    for index in range(len(formants)):
        nearby = max(floors[index - 1] if index else floors[0], floors[index])
        margin = 10 * math.log10(max(peaks[index] / nearby, 1e-9))
        worst = margin if worst is None else min(worst, margin)
    return worst


def render(text_wav, probe_wav, rate, settings):
    """Whisper a copy of `text_wav` under `settings`, and measure it."""
    (audiofx._BREATH_TILT_STAGES, audiofx._BREATH_TILT, audiofx._BREATH_HIGHPASS,
     audiofx._BREATH_HIGHPASS_POLES, audiofx._BREATH_LOWPASS) = settings
    work = tempfile.mkdtemp(prefix="train-whisper-")
    try:
        candidate = os.path.join(work, "candidate.wav")
        with open(text_wav, "rb") as src, open(candidate, "wb") as dst:
            dst.write(src.read())
        audiofx.apply_breath(candidate, 0.9)
        samples, _ = read_wav(candidate)
        return (spectrum(samples, rate), centroid(samples, rate),
                formant_margin(probe_wav, os.path.join(work, "v.wav"), rate))
    finally:
        for name in os.listdir(work):
            os.unlink(os.path.join(work, name))
        os.rmdir(work)


def distance(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def distance_to_all(spec, targets):
    return sum(distance(spec, target) for target in targets) / len(targets)


def current_settings():
    return (audiofx._BREATH_TILT_STAGES, audiofx._BREATH_TILT, audiofx._BREATH_HIGHPASS,
            audiofx._BREATH_HIGHPASS_POLES, audiofx._BREATH_LOWPASS)


def describe(settings):
    return ("stages %d, tilt %.2f, high-pass %.0f Hz x%d, low-pass %.0f Hz"
            % (settings[0], settings[1], settings[2], settings[3], settings[4]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refs", nargs="+", required=True,
                        help="whispered reference recordings, mono PCM wav")
    parser.add_argument("--voiced", required=True,
                        help="a wav of ordinary speech from the voice being shaped")
    parser.add_argument("--search", action="store_true",
                        help="try a grid of excitation shapes instead of only measuring")
    parser.add_argument("--floor", type=float, default=8.0,
                        help="reject settings whose worst formant margin falls below "
                             "this many dB (default: 8)")
    args = parser.parse_args(argv)

    paths = [p for pattern in args.refs for p in sorted(glob.glob(pattern)) or [pattern]]
    print("references")
    targets, target_centroid, rate = reference_profiles(paths)
    floor = agreement_floor(targets)
    print("\n  %d utterances, spectral centroid %.0f Hz" % (len(targets), target_centroid))
    print("  they sit %.2f dB from each other -- fitting closer than that is fitting "
          "noise" % floor)

    voiced, voiced_rate = read_wav(args.voiced)
    if voiced_rate != rate:
        raise SystemExit("--voiced is %d Hz but the references are %d Hz; resample one"
                         % (voiced_rate, rate))

    probe = os.path.join(tempfile.mkdtemp(prefix="train-whisper-probe-"), "vowel.wav")
    synthetic_vowel(probe, rate)                 # built once; every trial copies it

    base = current_settings()
    spec, bright, margin = render(args.voiced, probe, rate, base)
    print("\ncurrent: %s" % describe(base))
    print("  spectral error %.2f dB, centroid %.0f Hz, worst formant margin %.1f dB"
          % (distance_to_all(spec, targets), bright, margin))

    if not args.search:
        return 0

    grid = [(1, tilt, hp, poles, lp)
            for tilt in (0.0, 0.35, 0.55, 0.70, 0.85)
            for hp, poles in ((0.0, 1), (200.0, 2), (300.0, 2), (300.0, 3), (450.0, 3))
            for lp in (0.0, 3000.0, 4500.0, 6000.0)]
    print("\nsearching %d settings" % len(grid))
    scored = []
    for settings in grid:
        spec, bright, margin = render(args.voiced, probe, rate, settings)
        error = distance_to_all(spec, targets)
        # A decibel of band error and 400 Hz of centroid drift are treated as equally
        # bad. The exact exchange rate is a judgement call; what it is there to prevent
        # is a setting buying a better band fit with an audibly wrong brightness.
        score = error + abs(bright - target_centroid) / 400.0
        scored.append((score, error, bright, margin, settings))
        print("  %-52s error %5.2f dB  centroid %5.0f Hz  margin %5.1f dB%s"
              % (describe(settings), error, bright, margin,
                 "" if margin >= args.floor else "   (rejected)"))

    usable = [row for row in scored if row[3] >= args.floor]
    if not usable:
        print("\nnothing kept its formants; lower --floor only if you know why")
        return 1
    score, error, bright, margin, settings = min(usable)
    print("\nbest: %s" % describe(settings))
    print("  spectral error %.2f dB, centroid %.0f Hz (references %.0f), "
          "worst formant margin %.1f dB" % (error, bright, target_centroid, margin))
    print("\npaste into src/localtts/audiofx.py:")
    for name, value in (("_BREATH_TILT_STAGES", "%d" % settings[0]),
                        ("_BREATH_TILT", "%.2f" % settings[1]),
                        ("_BREATH_HIGHPASS", "%.1f" % settings[2]),
                        ("_BREATH_HIGHPASS_POLES", "%d" % settings[3]),
                        ("_BREATH_LOWPASS", "%.1f" % settings[4])):
        print("  %s = %s" % (name, value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
