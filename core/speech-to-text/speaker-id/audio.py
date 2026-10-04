# PCM decode, 24 kHz -> 16 kHz resampling, and energy-based speech trimming.
#
# The app's audio pipeline is 16-bit PCM mono at 24 kHz (see
# core/speech-to-text/public/index.html); WeSpeaker embedding models are
# trained on 16 kHz. The 24->16 conversion is a clean 2/3 rational resample:
# zero-stuff by 2, low-pass at the new Nyquist, take every 3rd sample.

import numpy as np

SOURCE_RATE = 24000
TARGET_RATE = 16000

# Windowed-sinc low-pass for the 2x-upsampled (48 kHz) signal, cutoff at the
# 16 kHz stream's Nyquist (8 kHz). Kaiser beta 8.6 ~= 90 dB stopband.
_TAPS = 121
_CUTOFF = 1.0 / 3.0  # 8 kHz as a fraction of the 24 kHz Nyquist


def _lowpass():
    n = np.arange(_TAPS) - (_TAPS - 1) / 2
    h = _CUTOFF * np.sinc(_CUTOFF * n) * np.kaiser(_TAPS, 8.6)
    return (2.0 * h / h.sum()).astype(np.float64)  # gain 2 restores upsampled level


_FIR = _lowpass()

# Speech gate: mirrors the providers' SPEECH_RMS = 0.008 (normalized) speech
# threshold, in int16 scale. Windows of 10 ms below the gate count as silence.
SPEECH_RMS = 0.008 * 32768.0
_GATE_HOP = 240          # 10 ms at 24 kHz
MAX_KEPT_GAP_S = 0.3     # internal silence longer than this is dropped


def decode_pcm16(data):
    """Raw little-endian 16-bit mono PCM at 24 kHz -> float64 in int16 scale."""
    if len(data) % 2:
        data = data[:-1]
    return np.frombuffer(data, dtype='<i2').astype(np.float64)


def resample_to_16k(wave24k):
    if len(wave24k) == 0:
        return wave24k
    up = np.zeros(len(wave24k) * 2, dtype=np.float64)
    up[::2] = wave24k
    filtered = np.convolve(up, _FIR)
    delay = (_TAPS - 1) // 2
    filtered = filtered[delay:delay + len(up)]
    return filtered[::3]


def trim_to_speech(wave24k):
    """Keep speech and short pauses, drop long silences and leading/trailing
    quiet. Returns (trimmed_wave, net_speech_seconds). Working at 24 kHz keeps
    the gate aligned with the providers' RMS numbers."""
    n_windows = len(wave24k) // _GATE_HOP
    if n_windows == 0:
        return wave24k[:0], 0.0
    windows = wave24k[:n_windows * _GATE_HOP].reshape(n_windows, _GATE_HOP)
    rms = np.sqrt((windows ** 2).mean(axis=1))
    speechy = rms >= SPEECH_RMS
    if not speechy.any():
        return wave24k[:0], 0.0

    max_gap = int(MAX_KEPT_GAP_S * SOURCE_RATE / _GATE_HOP)
    keep = np.zeros(n_windows, dtype=bool)
    speech_idx = np.flatnonzero(speechy)
    prev = None
    for i in speech_idx:
        keep[i] = True
        if prev is not None and i - prev <= max_gap:
            keep[prev:i] = True  # bridge a short pause
        prev = i

    kept = windows[keep].reshape(-1)
    return kept, float(speechy.sum() * _GATE_HOP / SOURCE_RATE)
