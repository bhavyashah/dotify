import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import matcher


def unit(v):
    v = np.asarray(v, dtype=np.float64)
    return v / np.linalg.norm(v)


class StoreRoundTrip(unittest.TestCase):
    def test_save_load_remove(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = matcher.VoiceprintStore(tmp)
            emb = unit(np.arange(1, 193))
            store.save('Guest Speaker', emb, 'campplus', 12.3)
            loaded = store.load()
            self.assertIn('Guest Speaker', loaded)
            np.testing.assert_allclose(loaded['Guest Speaker']['embedding'], emb, atol=1e-6)
            self.assertTrue(store.remove('Guest Speaker'))
            self.assertEqual(store.load(), {})

    def test_load_reparses_only_when_files_change(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            store = matcher.VoiceprintStore(tmp)
            store.save('Guest', unit(np.arange(1, 9)), 'm', 10.0)
            first = store.load()
            with mock.patch.object(matcher.json, 'loads',
                                   side_effect=AssertionError('re-parsed')):
                self.assertEqual(store.load().keys(), first.keys())
            store.save('Guest', unit(np.arange(9, 1, -1)), 'm', 11.0)
            self.assertEqual(store.load()['Guest']['seconds'], 11.0)
            store.save('Host', unit(np.arange(2, 10)), 'm', 9.0)
            self.assertEqual(set(store.load()), {'Guest', 'Host'})
            store.remove('Guest')
            self.assertEqual(set(store.load()), {'Host'})

    def test_bad_file_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'junk.json').write_text('not json', encoding='utf-8')
            self.assertEqual(matcher.VoiceprintStore(tmp).load(), {})


class Names(unittest.TestCase):
    def test_valid(self):
        for name in ('Guest', "O'Neil", 'Mary Ann', 'J-P'):
            self.assertTrue(matcher.valid_name(name))
        for name in ('', ' lead', '1abc', 'x' * 41, 'a\nb', None):
            self.assertFalse(matcher.valid_name(name))


class Matching(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = matcher.VoiceprintStore(self.tmp.name)
        # Two well-separated voiceprints.
        self.guest = unit([1.0, 0.1] + [0.0] * 190)
        self.host = unit([0.1, 1.0] + [0.0] * 190)
        self.store.save('Guest', self.guest, 'test', 20)
        self.store.save('Host', self.host, 'test', 20)
        self.session = matcher.SessionMatcher(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_needs_min_speech_before_assigning(self):
        result = self.session.identify('A', self.guest, 1.0)
        self.assertIsNone(result['name'])
        result = self.session.identify('A', self.guest, 2.5)  # total 3.5 s
        self.assertEqual(result['name'], 'Guest')
        self.assertTrue(result['assigned'])

    def test_sticky_assignment(self):
        self.session.identify('A', self.guest, 4.0)
        # Later turns keep the name even with a garbage embedding.
        result = self.session.identify('A', unit(np.ones(192)), 2.0)
        self.assertEqual(result['name'], 'Guest')

    def test_two_labels_two_names(self):
        self.assertEqual(self.session.identify('A', self.guest, 4.0)['name'], 'Guest')
        self.assertEqual(self.session.identify('B', self.host, 4.0)['name'], 'Host')

    def test_name_not_reused_across_labels(self):
        self.session.identify('A', self.guest, 4.0)
        # A second label matching the guest must NOT get that name too.
        result = self.session.identify('B', self.guest, 4.0)
        self.assertIsNone(result['name'])

    def test_unenrolled_voice_stays_unnamed(self):
        stranger = unit([0.0] * 190 + [1.0, 0.2])
        result = self.session.identify('A', stranger, 5.0)
        self.assertIsNone(result['name'])
        self.assertLess(result['score'], matcher.THRESHOLD)

    def test_margin_blocks_ambiguous_match(self):
        # A voice similar to both enrolled speakers: score clears the threshold
        # but the margin doesn't -> no name.
        between = unit(np.asarray(self.guest) + np.asarray(self.host))
        result = self.session.identify('A', between, 5.0)
        self.assertIsNone(result['name'])
        self.assertGreater(result['score'], matcher.THRESHOLD)
        self.assertLess(result['margin'], matcher.MARGIN)

    def test_centroid_recovers_from_noisy_turn(self):
        rng = np.random.default_rng(3)
        noisy = unit(np.asarray(self.guest) + 0.8 * rng.standard_normal(192))
        self.session.identify('A', noisy, 1.0)
        # Plenty of clean guest audio afterwards outweighs the noisy first turn.
        result = self.session.identify('A', self.guest, 6.0)
        self.assertEqual(result['name'], 'Guest')

    def test_reset_clears_session_not_store(self):
        self.session.identify('A', self.guest, 4.0)
        self.session.reset()
        self.assertEqual(self.session.assigned, {})
        result = self.session.identify('B', self.guest, 4.0)
        self.assertEqual(result['name'], 'Guest')  # store still enrolled


if __name__ == '__main__':
    unittest.main()
