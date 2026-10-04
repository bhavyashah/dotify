import json
from pathlib import Path
import unittest

import display_profiles as profiles

ROOT = Path(__file__).parent


class Collection:
    def __init__(self, vid, pid, usage_page, output_length=24, feature_length=39):
        self.vid = vid
        self.pid = pid
        self.usage_page = usage_page
        self.output_length = output_length
        self.feature_length = feature_length


class DisplayProfileTests(unittest.TestCase):
    def test_registry_has_requested_humanware_devices(self):
        expected = {
            "nls-ereader": (0x1C71, 0xCE01),
            "brailliant-bi-20x": (0x1C71, 0xC141),
            "braillenote-touch": (0x1C71, 0xC00A),
            "braillenote-touch-v2": (0x1C71, 0xC00E),
        }
        actual = {
            profile.id: (profile.vid, profile.pid)
            for profile in profiles.native_hid_profiles()
        }
        self.assertTrue(expected.items() <= actual.items())
        self.assertEqual(10, len(actual))

    def test_braillesense_is_discovery_only_not_claimed_as_native_hid(self):
        profile = profiles.get_profile("braillesense-legacy-usb")
        self.assertEqual(profiles.HIMS_CUSTOM_BULK, profile.transport)
        self.assertFalse(profile.native_supported)
        self.assertNotIn(profile, profiles.native_hid_profiles())

    def test_safe_native_transport_expansion_is_partitioned(self):
        self.assertEqual(1, len(profiles.hims_hid_profiles()))
        self.assertEqual(4, len(profiles.serial_profiles()))
        self.assertEqual(15, len(profiles.native_profiles()))
        self.assertEqual(2, len(profiles.discovery_profiles()))

    def test_hims_hid_fallback_requires_caps_and_output_reports(self):
        profile = profiles.get_profile("hims-braille-edge-3s")
        right = Collection(profile.vid, profile.pid, 0xFF00, 41, 16)
        no_caps = Collection(profile.vid, profile.pid, 0xFF00, 41, 9)
        self.assertEqual((right,), profiles.matching_collections(profile, (right, no_caps)))

    def test_modern_humanware_profile_requires_usage_page_93(self):
        profile = profiles.get_profile("brailliant-bi-20x")
        right = Collection(profile.vid, profile.pid, 0x93)
        wrong = Collection(profile.vid, profile.pid, 0x41)
        self.assertEqual((right,), profiles.matching_collections(profile, (right, wrong)))

    def test_legacy_touch_fallback_requires_humanware_descriptor_shape(self):
        profile = profiles.get_profile("braillenote-touch")
        right = Collection(profile.vid, profile.pid, 0xFF00, 36, 39)
        no_feature = Collection(profile.vid, profile.pid, 0xFF00, 36, 0)
        too_short = Collection(profile.vid, profile.pid, 0xFF00, 4, 39)
        self.assertEqual(
            (right,), profiles.matching_collections(profile, (right, no_feature, too_short))
        )

    def test_launcher_can_consume_registry_without_python(self):
        document = json.loads((ROOT / "display_profiles.json").read_text(encoding="utf-8"))
        supported = [item for item in document["profiles"] if item["native_supported"]]
        self.assertEqual(15, len(supported))
        self.assertTrue(all(len(item["vid"]) == 4 and len(item["pid"]) == 4 for item in supported))

    def test_launcher_uses_registry_instead_of_one_hardcoded_pid(self):
        launcher = (ROOT.parent / "launcher.ps1").read_text(encoding="utf-8")
        self.assertIn("display_profiles.json", launcher)
        self.assertIn("$profile.vid", launcher)
        self.assertNotIn("VID_1C71&PID_CE01", launcher)


class BluetoothProfileTests(unittest.TestCase):
    def test_bluetooth_registry_covers_both_protocol_families(self):
        by_id = {profile.id: profile for profile in profiles.bluetooth_profiles()}
        self.assertEqual({"humanware-bluetooth", "hims-bluetooth"}, set(by_id))
        self.assertEqual(
            profiles.HUMANWARE_BLUETOOTH, by_id["humanware-bluetooth"].transport
        )
        self.assertEqual(profiles.HIMS_BLUETOOTH, by_id["hims-bluetooth"].transport)

    def test_name_match_is_case_insensitive_prefix_not_substring(self):
        humanware = profiles.get_profile("humanware-bluetooth")
        self.assertTrue(humanware.matches_device_name("NLS eReader H1234567"))
        self.assertTrue(humanware.matches_device_name("brailliant bi 40x 456789"))
        self.assertTrue(humanware.matches_device_name("  APH Mantis Q40 1  "))
        self.assertFalse(humanware.matches_device_name("Braille EDGE 40"))
        self.assertFalse(humanware.matches_device_name("My NLS eReader"))
        self.assertFalse(humanware.matches_device_name(""))

    def test_real_world_paired_names_from_reference_drivers_match(self):
        # NVDA's Bluetooth detection prefixes are the reference: HIMS units
        # broadcast unspaced names ('SmartBeetle(B201...)'), and the
        # BrailleOne can pair under its 'Humanware BrailleOne' product name.
        hims = profiles.get_profile("hims-bluetooth")
        self.assertTrue(hims.matches_device_name("SmartBeetle(B2010042)"))
        self.assertTrue(hims.matches_device_name("BrailleSense6 123"))
        self.assertTrue(hims.matches_device_name("BrailleEDGE40 S/N123"))
        humanware = profiles.get_profile("humanware-bluetooth")
        self.assertTrue(humanware.matches_device_name("Humanware BrailleOne 87654321"))

    def test_no_prefix_matches_two_profiles(self):
        # Load-time validation enforces this; assert it holds for the
        # shipped registry so a future edit cannot silently overlap.
        lowered = [
            (profile.id, prefix.lower())
            for profile in profiles.BLUETOOTH_PROFILES
            for prefix in profile.name_prefixes
        ]
        for id_a, prefix_a in lowered:
            for id_b, prefix_b in lowered:
                if id_a != id_b:
                    self.assertFalse(
                        prefix_b.startswith(prefix_a),
                        f"{prefix_a!r} ({id_a}) also matches {id_b}",
                    )

    def test_bluetooth_ids_share_the_usb_id_namespace(self):
        usb_ids = {profile.id for profile in profiles.PROFILES}
        bluetooth_ids = {profile.id for profile in profiles.BLUETOOTH_PROFILES}
        self.assertEqual(set(), usb_ids & bluetooth_ids)
        self.assertEqual(
            profiles.HIMS_BLUETOOTH, profiles.get_profile("hims-bluetooth").transport
        )

    def test_usb_accessors_do_not_leak_bluetooth_profiles(self):
        for accessor in (
            profiles.native_profiles,
            profiles.native_hid_profiles,
            profiles.hims_hid_profiles,
            profiles.serial_profiles,
            profiles.discovery_profiles,
        ):
            for profile in accessor():
                self.assertNotIn(profile.transport, profiles.BLUETOOTH_TRANSPORTS)

    def test_launcher_accepts_bluetooth_profile_ids(self):
        launcher = (ROOT.parent / "launcher.ps1").read_text(encoding="utf-8")
        self.assertIn("bluetooth_profiles", launcher)


if __name__ == "__main__":
    unittest.main()
