import unittest

import hims_protocol as hims


class HimsProtocolTests(unittest.TestCase):
    def test_display_packet_matches_nvda_framing(self):
        packet = hims.build_display_packet([1, 2, 3])
        self.assertEqual(b"\xFC\xFC\x01\xF0\x03\x00\x01\x02\x03\xF1\xF2", packet[:11])
        self.assertEqual(b"\x00\x00\xF3" + bytes(4), packet[11:18])
        self.assertEqual(b"\xFD\xFD", packet[-2:])
        self.assertTrue(hims.checksum_is_valid(packet))

    def test_cell_count_request_has_32_zero_data_bytes(self):
        packet = hims.build_cell_count_request()
        self.assertEqual(b"\xFB\xFB\x01\xF0\x20\x00", packet[:6])
        self.assertEqual(bytes(32), packet[6:38])
        self.assertTrue(hims.checksum_is_valid(packet))

    def test_rejects_invalid_cells(self):
        with self.assertRaises(ValueError):
            hims.build_display_packet([])
        with self.assertRaises(ValueError):
            hims.build_display_packet([256])


if __name__ == "__main__":
    unittest.main()
