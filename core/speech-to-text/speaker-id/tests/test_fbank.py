# fbank correctness. The strong test compares against
# torchaudio.compliance.kaldi.fbank — the exact frontend WeSpeaker models were
# trained with — and skips when torchaudio isn't installed (it is NOT a
# runtime dependency; install it ad hoc to re-verify after touching fbank.py).
# The remaining tests pin invariants that catch gross regressions without it.

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fbank


def _speechlike(seconds=1.0, seed=1):
    rng = np.random.default_rng(seed)
    t = np.arange(int(16000 * seconds)) / 16000
    wave = (4000 * np.sin(2 * np.pi * 220 * t)
            + 2000 * np.sin(2 * np.pi * 700 * t + 1.0)
            + 300 * rng.standard_normal(len(t)))
    return wave.astype(np.float64)


class FbankShape(unittest.TestCase):
    def test_frame_count_snip_edges(self):
        self.assertEqual(fbank.num_frames(399), 0)
        self.assertEqual(fbank.num_frames(400), 1)
        self.assertEqual(fbank.num_frames(560), 2)
        self.assertEqual(fbank.fbank(_speechlike(1.0)).shape, (98, 80))

    def test_deterministic(self):
        wave = _speechlike()
        np.testing.assert_array_equal(fbank.fbank(wave), fbank.fbank(wave))

    def test_energy_peak_at_tone(self):
        # The 220/700 Hz tones must put the peak in the lower third of bins.
        feats = fbank.fbank(_speechlike()).mean(axis=0)
        self.assertLess(int(np.argmax(feats)), 27)

    def test_amplitude_shifts_log_energy(self):
        # 2x amplitude ~= +2*ln(2) in log-power features.
        wave = _speechlike()
        delta = fbank.fbank(2 * wave).mean() - fbank.fbank(wave).mean()
        self.assertAlmostEqual(delta, 2 * np.log(2), places=2)


class FbankMatchesTorchaudio(unittest.TestCase):
    def test_matches_kaldi_reference(self):
        try:
            import torch
            import torchaudio.compliance.kaldi as kaldi
        except ImportError:
            self.skipTest('torchaudio not installed (dev-only reference check)')
        wave = _speechlike(2.0)
        ours = fbank.fbank(wave)
        reference = kaldi.fbank(
            torch.from_numpy(wave).float().unsqueeze(0),
            num_mel_bins=80, frame_length=25, frame_shift=10,
            dither=0.0, sample_frequency=16000, energy_floor=0.0,
        ).numpy()
        self.assertEqual(ours.shape, reference.shape)
        # Feature values span roughly [-3, 25]; agree to ~1e-3.
        self.assertLess(np.abs(ours - reference).max(), 2e-3)


if __name__ == '__main__':
    unittest.main()
