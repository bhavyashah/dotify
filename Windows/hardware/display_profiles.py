"""Single source of truth for native Windows braille-display identities."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable, Protocol


HUMANWARE_HID = "humanware-hid"
HUMANWARE_SERIAL = "humanware-serial"
HIMS_HID = "hims-hid"
HIMS_SERIAL = "hims-serial"
HIMS_CUSTOM_BULK = "hims-custom-bulk"
HUMANWARE_BLUETOOTH = "humanware-bluetooth"
HIMS_BLUETOOTH = "hims-bluetooth"

BLUETOOTH_TRANSPORTS = (HUMANWARE_BLUETOOTH, HIMS_BLUETOOTH)


class HidCollection(Protocol):
    vid: int
    pid: int
    usage_page: int
    output_length: int
    feature_length: int


@dataclass(frozen=True)
class DisplayProfile:
    id: str
    name: str
    vid: int
    pid: int
    transport: str
    usage_pages: tuple[int, ...]
    native_supported: bool
    hardware_verified: bool
    terminal_instructions: str
    allow_descriptor_fallback: bool = False

    def matches_identity(self, vid: int, pid: int) -> bool:
        return (vid, pid) == (self.vid, self.pid)

    def matches_collection(self, collection: HidCollection) -> bool:
        if not self.matches_identity(collection.vid, collection.pid):
            return False
        if self.usage_pages:
            return collection.usage_page in self.usage_pages
        if self.allow_descriptor_fallback and self.transport == HUMANWARE_HID:
            # Legacy HumanWare devices do not consistently advertise page 0x93.
            # Their protocol still requires caps byte 24 and four output bytes
            # of framing, so use those descriptor minima to avoid a blind PID-
            # only selection.
            return collection.feature_length >= 25 and collection.output_length >= 5
        if self.allow_descriptor_fallback and self.transport == HIMS_HID:
            # NVDA's HIMS HID transport reads feature report 1 (cell count at
            # byte 9) and sends one length/report byte plus the cells.
            return collection.feature_length >= 10 and collection.output_length >= 2
        return False


@dataclass(frozen=True)
class BluetoothProfile:
    """A Bluetooth display family, identified by its paired-device NAME.

    Bluetooth SPP ports carry no USB VID/PID, so the identity registered
    here is the name the display broadcasts while pairing (prefix-matched,
    the same discipline BRLTTY and NVDA use). One profile covers a protocol
    family, not a single model.
    """

    id: str
    name: str
    transport: str
    name_prefixes: tuple[str, ...]
    native_supported: bool
    hardware_verified: bool
    terminal_instructions: str

    def matches_device_name(self, device_name: str) -> bool:
        candidate = device_name.strip().lower()
        return any(
            candidate.startswith(prefix.lower()) for prefix in self.name_prefixes
        )


def _hex_id(value: object, field: str) -> int:
    if not isinstance(value, str) or len(value) != 4:
        raise RuntimeError(f"profile {field} must be a four-digit hexadecimal string")
    try:
        return int(value, 16)
    except ValueError as error:
        raise RuntimeError(f"profile {field} is not hexadecimal: {value!r}") from error


def _load_registry() -> tuple[tuple[DisplayProfile, ...], tuple[BluetoothProfile, ...]]:
    path = Path(__file__).with_name("display_profiles.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != 1:
        raise RuntimeError("unsupported display profile schema")

    profiles = []
    for item in document.get("profiles", []):
        profiles.append(
            DisplayProfile(
                id=item["id"],
                name=item["name"],
                vid=_hex_id(item["vid"], "vid"),
                pid=_hex_id(item["pid"], "pid"),
                transport=item["transport"],
                usage_pages=tuple(_hex_id(value, "usage_page") for value in item["usage_pages"]),
                native_supported=bool(item["native_supported"]),
                hardware_verified=bool(item["hardware_verified"]),
                terminal_instructions=item["terminal_instructions"],
                allow_descriptor_fallback=bool(item.get("allow_descriptor_fallback", False)),
            )
        )

    bluetooth = []
    for item in document.get("bluetooth_profiles", []):
        raw_prefixes = item["name_prefixes"]
        if not isinstance(raw_prefixes, (list, tuple)):
            # A bare string would silently explode into per-character
            # prefixes that match nearly every paired device name.
            raise RuntimeError(
                f"bluetooth profile {item.get('id')!r} name_prefixes must be "
                "a list of strings"
            )
        prefixes = tuple(raw_prefixes)
        if not prefixes or not all(
            isinstance(prefix, str) and prefix.strip() for prefix in prefixes
        ):
            raise RuntimeError(
                f"bluetooth profile {item.get('id')!r} needs nonempty name_prefixes"
            )
        if item["transport"] not in BLUETOOTH_TRANSPORTS:
            raise RuntimeError(
                f"bluetooth profile {item.get('id')!r} has non-Bluetooth transport "
                f"{item['transport']!r}"
            )
        bluetooth.append(
            BluetoothProfile(
                id=item["id"],
                name=item["name"],
                transport=item["transport"],
                name_prefixes=prefixes,
                native_supported=bool(item["native_supported"]),
                hardware_verified=bool(item["hardware_verified"]),
                terminal_instructions=item["terminal_instructions"],
            )
        )

    ids = [profile.id for profile in profiles] + [profile.id for profile in bluetooth]
    identities = [(profile.vid, profile.pid) for profile in profiles]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate display profile id")
    if len(identities) != len(set(identities)):
        raise RuntimeError("duplicate display USB identity")

    # The Bluetooth analog of the duplicate-USB-identity guard: a paired
    # name must identify exactly one profile, so no profile's prefix may be
    # a prefix of (or equal to) another profile's — matching is startswith.
    lowered = [
        (profile.id, prefix.lower())
        for profile in bluetooth
        for prefix in profile.name_prefixes
    ]
    for id_a, prefix_a in lowered:
        for id_b, prefix_b in lowered:
            if id_a != id_b and prefix_b.startswith(prefix_a):
                raise RuntimeError(
                    f"bluetooth name prefix {prefix_a!r} ({id_a}) would also "
                    f"match {id_b} devices (prefix {prefix_b!r}); prefixes "
                    "must identify exactly one profile"
                )
    return tuple(profiles), tuple(bluetooth)


PROFILES, BLUETOOTH_PROFILES = _load_registry()


def get_profile(profile_id: str) -> DisplayProfile | BluetoothProfile:
    try:
        return next(
            profile
            for profile in (*PROFILES, *BLUETOOTH_PROFILES)
            if profile.id == profile_id
        )
    except StopIteration as error:
        choices = ", ".join(
            profile.id for profile in (*PROFILES, *BLUETOOTH_PROFILES)
        )
        raise ValueError(f"unknown display profile {profile_id!r}; choose one of: {choices}") from error


def native_hid_profiles() -> tuple[DisplayProfile, ...]:
    return tuple(
        profile
        for profile in PROFILES
        if profile.native_supported and profile.transport == HUMANWARE_HID
    )


def hims_hid_profiles() -> tuple[DisplayProfile, ...]:
    return tuple(
        profile
        for profile in PROFILES
        if profile.native_supported and profile.transport == HIMS_HID
    )


def serial_profiles() -> tuple[DisplayProfile, ...]:
    return tuple(
        profile
        for profile in PROFILES
        if profile.native_supported
        and profile.transport in (HUMANWARE_SERIAL, HIMS_SERIAL)
    )


def bluetooth_profiles() -> tuple[BluetoothProfile, ...]:
    return tuple(
        profile for profile in BLUETOOTH_PROFILES if profile.native_supported
    )


def native_profiles() -> tuple[DisplayProfile, ...]:
    return tuple(profile for profile in PROFILES if profile.native_supported)


def discovery_profiles() -> tuple[DisplayProfile, ...]:
    return tuple(profile for profile in PROFILES if not profile.native_supported)


def profiles_for_identity(vid: int, pid: int) -> tuple[DisplayProfile, ...]:
    return tuple(profile for profile in PROFILES if profile.matches_identity(vid, pid))


def matching_collections(
    profile: DisplayProfile, collections: Iterable[HidCollection]
) -> tuple[HidCollection, ...]:
    return tuple(collection for collection in collections if profile.matches_collection(collection))
