# Kaldi-compatible 80-bin log mel filterbank features, implemented with numpy
# only. WeSpeaker ONNX embedding models expect features identical to
# torchaudio.compliance.kaldi.fbank(wav, num_mel_bins=80, frame_length=25,
# frame_shift=10, dither=0, sample_frequency=16000) computed on int16-scaled
# samples — the exact frontend wespeaker/bin/infer_onnx.py uses. Any deviation
# (mel scale, window, padding) silently degrades embedding quality, so this
# file mirrors Kaldi's fbank pipeline step for step and is verified against
# torchaudio in tests/test_fbank.py (the test skips when torchaudio is not
# installed; it was run against torchaudio during development).

import numpy as np

SAMPLE_RATE = 16000
NUM_MEL_BINS = 80
FRAME_LENGTH = 400   # 25 ms at 16 kHz
FRAME_SHIFT = 160    # 10 ms
PADDED_LENGTH = 512  # next power of two (Kaldi round_to_power_of_two)
PREEMPHASIS = 0.97
LOW_FREQ = 20.0      # Kaldi default mel-bank low cutoff


def _mel(freq):
    return 1127.0 * np.log(1.0 + freq / 700.0)


def _mel_banks():
    # Kaldi MelBanks: num_bins triangular filters over FFT bins 0..N/2-1,
    # equally spaced on the mel scale between LOW_FREQ and the Nyquist.
    fft_bins = PADDED_LENGTH // 2
    fft_bin_width = SAMPLE_RATE / PADDED_LENGTH
    mel_low = _mel(LOW_FREQ)
    mel_high = _mel(SAMPLE_RATE / 2.0)
    delta = (mel_high - mel_low) / (NUM_MEL_BINS + 1)
    bin_mels = _mel(np.arange(fft_bins) * fft_bin_width)  # mel of each FFT bin

    banks = np.zeros((NUM_MEL_BINS, fft_bins), dtype=np.float64)
    for i in range(NUM_MEL_BINS):
        left = mel_low + i * delta
        center = left + delta
        right = center + delta
        up = (bin_mels - left) / (center - left)
        down = (right - bin_mels) / (right - center)
        banks[i] = np.clip(np.minimum(up, down), 0.0, None)
    return banks


_BANKS = _mel_banks()
_WINDOW = (0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(FRAME_LENGTH)
                              / (FRAME_LENGTH - 1))) ** 0.85  # Kaldi "povey"


def num_frames(num_samples):
    # Kaldi snip_edges=True frame count.
    if num_samples < FRAME_LENGTH:
        return 0
    return 1 + (num_samples - FRAME_LENGTH) // FRAME_SHIFT


def fbank(wave):
    """wave: 1-D float array in int16 scale (-32768..32767) at 16 kHz.
    Returns (num_frames, 80) float32 log-mel features (no mean subtraction)."""
    wave = np.asarray(wave, dtype=np.float64)
    n = num_frames(len(wave))
    if n == 0:
        return np.zeros((0, NUM_MEL_BINS), dtype=np.float32)

    idx = (np.arange(FRAME_LENGTH)[None, :]
           + FRAME_SHIFT * np.arange(n)[:, None])
    frames = wave[idx]

    # Kaldi per-frame pipeline: remove DC offset, pre-emphasis (first sample
    # against itself), povey window.
    frames = frames - frames.mean(axis=1, keepdims=True)
    emphasized = np.empty_like(frames)
    emphasized[:, 1:] = frames[:, 1:] - PREEMPHASIS * frames[:, :-1]
    emphasized[:, 0] = frames[:, 0] - PREEMPHASIS * frames[:, 0]
    windowed = emphasized * _WINDOW

    spectrum = np.fft.rfft(windowed, n=PADDED_LENGTH, axis=1)
    power = (spectrum.real ** 2 + spectrum.imag ** 2)[:, :PADDED_LENGTH // 2]

    mel_energies = power @ _BANKS.T
    eps = np.finfo(np.float32).eps
    return np.log(np.maximum(mel_energies, eps)).astype(np.float32)
