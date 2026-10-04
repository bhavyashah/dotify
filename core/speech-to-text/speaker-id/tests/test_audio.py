import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import audio


class Resample(unittest.TestCase):
    def test_length_ratio(self):
        out = audio.resample_to_16k(np.zeros(24000))
        self.assertEqual(len(out), 16000)

    def test_tone_preserved(self):
        # A 1 kHz tone must come through at 1 kHz with ~unity gain.
        t = np.arange(24000) / 24000
        tone = 10000 * np.sin(2 * np.pi * 1000 * t)
        out = audio.resample_to_16k(tone)
        spectrum = np.abs(np.fft.rfft(out[2000:14000]))
        peak_hz = np.argmax(spectrum) * 16000 / 12000
        self.assertAlmostEqual(peak_hz, 1000, delta=3)
        rms_ratio = np.sqrt((out[2000:14000] ** 2).mean()) / np.sqrt((tone ** 2).mean())
        self.assertAlmostEqual(rms_ratio, 1.0, delta=0.02)

    def test_high_band_rejected(self):
        # 11 kHz is above the 16 kHz stream's Nyquist: must be attenuated hard,
        # not aliased into the passband.
        t = np.arange(24000) / 24000
        tone = 10000 * np.sin(2 * np.pi * 11000 * t)
        out = audio.resample_to_16k(tone)
        self.assertLess(np.abs(out[2000:14000]).max(), 100)  # > 40 dB down


class TrimToSpeech(unittest.TestCase):
    def _pcm(self, wave):
        return wave.astype(np.float64)

    def test_silence_only(self):
        trimmed, seconds = audio.trim_to_speech(np.zeros(48000))
        self.assertEqual(len(trimmed), 0)
        self.assertEqual(seconds, 0.0)

    def test_trims_silence_keeps_speech(self):
        rng = np.random.default_rng(7)
        loud = 3000 * rng.standard_normal(24000)          # 1 s "speech"
        clip = np.concatenate([np.zeros(24000), loud, np.zeros(24000)])
        trimmed, seconds = audio.trim_to_speech(clip)
        self.assertAlmostEqual(seconds, 1.0, delta=0.05)
        self.assertLess(len(trimmed), 1.3 * 24000)

    def test_short_gap_bridged(self):
        rng = np.random.default_rng(7)
        talk = lambda: 3000 * rng.standard_normal(12000)  # 0.5 s
        gap = np.zeros(4800)                              # 0.2 s pause: kept
        clip = np.concatenate([talk(), gap, talk()])
        trimmed, seconds = audio.trim_to_speech(clip)
        self.assertGreaterEqual(len(trimmed), len(clip) - 480)
        self.assertAlmostEqual(seconds, 1.0, delta=0.05)


if __name__ == '__main__':
    unittest.main()
