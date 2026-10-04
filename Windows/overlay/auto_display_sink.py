"""The sink the launcher uses: finds the one connected display and wraps its
transport's sink (HumanWare or HIMS HID, standard HID, USB serial, or
Bluetooth SPP).

USB wins over Bluetooth: a paired display's virtual COM port exists even
while the unit is off or out of range, so Bluetooth is tried only when no
USB display is present (or when pinned explicitly).
"""

from __future__ import annotations

import sys
import threading

import display_profiles as profiles
import windows_hid as hid
from hims_hid_sink import HimsHidSink
from native_hid_sink import NativeHidSink
from sink_reconnect import DEFAULT_RECONNECT_ATTEMPTS, forget_held_chord
from serial_sinks import (
    HimsBluetoothSink,
    HimsSerialSink,
    HumanWareBluetoothSink,
    HumanWareSerialSink,
    enumerate_bluetooth_displays,
    enumerate_serial_displays,
)
from standard_hid_sink import StandardHidSink


class AutoDisplaySink:
    def __init__(self, *, profile_id=None,
                 reconnect_attempts=DEFAULT_RECONNECT_ATTEMPTS, api=None,
                 ports_provider=None, bt_ports_provider=None):
        self._requested_profile_id = profile_id
        self._reconnect_attempts = reconnect_attempts
        self._api = api
        self._ports_provider = ports_provider
        self._bt_ports_provider = bt_ports_provider
        self._sink = None
        self._keys_changed = None
        # Once a session has connected over USB, recovery never falls back to
        # a merely-paired Bluetooth unit (its port exists even while it is
        # off); it waits for the USB display to return.
        self._usb_session = False
        # Set by close(); a dial still in flight at quit closes its fresh
        # handle instead of binding it. Guarded by _state_lock.
        self._state_lock = threading.Lock()
        self._closing = False
        # Serializes discover/rebuild, since the cold watch can dial while the
        # engine's retry loops do.
        self._connect_lock = threading.RLock()

    @property
    def display_name(self):
        return getattr(self._sink, "display_name", None)

    @property
    def connected(self):
        """Cold-watch surface: a live transport is bound right now."""
        return self._sink is not None

    @property
    def device_identity(self):
        """Cold-watch surface: which physical device this session is on —
        ('hid', interface path lowercased) or ('com', 'COMn'); None while
        disconnected and for Bluetooth (a paired unit's COM port persists
        while it is off, so removal events can never mean a BT death)."""
        sink = self._sink
        if sink is None:
            return None
        return getattr(sink, "device_identity", None)

    def drop_cold(self):
        """The cold watch saw this session's device leave while nothing was
        being written. Close the dead transport so writes fail honestly and
        the key listener stops. Holds _connect_lock so it can't interleave
        with a rebuild."""
        with self._connect_lock:
            self._teardown_sink()

    @property
    def profile_id(self):
        return getattr(self._sink, "profile_id", None)

    def _from_profile(self, profile):
        common = {"profile_id": profile.id, "reconnect_attempts": self._reconnect_attempts}
        if profile.transport == profiles.HUMANWARE_HID:
            return NativeHidSink(api=self._api, **common)
        if profile.transport == profiles.HIMS_HID:
            return HimsHidSink(api=self._api, **common)
        if profile.transport == profiles.HUMANWARE_SERIAL:
            return HumanWareSerialSink(ports_provider=self._ports_provider, **common)
        if profile.transport == profiles.HIMS_SERIAL:
            return HimsSerialSink(ports_provider=self._ports_provider, **common)
        if profile.transport == profiles.HUMANWARE_BLUETOOTH:
            return HumanWareBluetoothSink(
                bt_ports_provider=self._bt_ports_provider, **common
            )
        if profile.transport == profiles.HIMS_BLUETOOTH:
            return HimsBluetoothSink(
                bt_ports_provider=self._bt_ports_provider, **common
            )
        raise RuntimeError(
            f"{profile.name} uses {profile.transport}; it needs a compatible signed Windows driver"
        )

    def _discover(self):
        if self._api is None:
            self._api = hid.WinApi()
        collections = hid.enumerate_hid_collections(self._api)
        candidates = []
        registered_hid_identities = {
            (profile.vid, profile.pid)
            for profile in (*profiles.native_hid_profiles(), *profiles.hims_hid_profiles())
        }
        for profile in (*profiles.native_hid_profiles(), *profiles.hims_hid_profiles()):
            if profiles.matching_collections(profile, collections):
                candidates.append((profile.name, self._from_profile(profile)))

        standard_identities = set()
        for collection in collections:
            if collection.usage_page != hid.STANDARD_BRAILLE_USAGE_PAGE:
                continue
            if (collection.vid, collection.pid) in registered_hid_identities:
                continue
            try:
                hid.standard_braille_width(collection)
            except RuntimeError:
                continue
            standard_identities.add((collection.vid, collection.pid))
        for vid, pid in standard_identities:
            candidates.append(
                (
                    f"standard HID {vid:04X}:{pid:04X}",
                    StandardHidSink(
                        api=self._api,
                        identity=(vid, pid),
                        reconnect_attempts=self._reconnect_attempts,
                    ),
                )
            )

        for profile, _port in enumerate_serial_displays(self._ports_provider):
            candidates.append((profile.name, self._from_profile(profile)))

        # Bluetooth is the fallback tier, consulted only when USB found
        # nothing: a paired display's COM port persists even while the unit
        # is off, so a Bluetooth candidate must never block (or tie with) a
        # live USB unit. The note keeps the no-display error honest about
        # what was actually checked.
        via_bluetooth = False
        bluetooth_note = "Paired Bluetooth serial displays were also checked. "
        if not candidates:
            if self._usb_session:
                bluetooth_note = (
                    "Bluetooth was not consulted because this session's "
                    "display is USB; reconnect it, or restart Dotify to "
                    "switch to Bluetooth. "
                )
            else:
                try:
                    bluetooth = enumerate_bluetooth_displays(self._bt_ports_provider)
                except Exception as error:
                    bluetooth = ()
                    bluetooth_note = f"Bluetooth discovery failed ({error}). "
                    print(f"[warn] Bluetooth display discovery failed ({error})",
                          file=sys.stderr, flush=True)
                via_bluetooth = bool(bluetooth)
                for profile, port in bluetooth:
                    candidates.append(
                        (f"{profile.name} [{port.name}]", self._from_profile(profile))
                    )

        # A profile may expose more than one matching collection, but it is one
        # candidate here; its protocol sink performs the stricter collection
        # ambiguity check when opening.
        unique = {}
        for name, sink in candidates:
            unique[(name, type(sink).__name__)] = (name, sink)
        candidates = list(unique.values())
        if not candidates:
            blocked = ", ".join(profile.name for profile in profiles.discovery_profiles())
            # The action comes first: the panel speaks only the first
            # sentence of a long reason and keeps the rest under Connection
            # details.
            action = (
                "Reconnect the USB braille display; Dotify reconnects the "
                "moment it is back. "
                if self._usb_session else
                "Connect a braille display by USB, or pair it by Bluetooth; "
                "Dotify connects the moment one appears. "
            )
            raise RuntimeError(
                action +
                "No compatible display was found. Standard HID, registered "
                "HumanWare/HIMS HID, and registered USB serial were checked. "
                f"{bluetooth_note}"
                f"Custom-bulk devices remain discovery-only: {blocked}"
            )
        if len(candidates) > 1:
            if via_bluetooth:
                # All candidates are Bluetooth pairings (the tier only runs
                # when USB found nothing), and a family profile pin cannot
                # split two paired units — "select one explicitly" would be
                # a dead end here, so give the actionable advice directly.
                raise RuntimeError(
                    "multiple paired Bluetooth braille displays match ("
                    + ", ".join(name for name, _ in candidates)
                    + "); unpair the ones not in use"
                )
            raise RuntimeError(
                "multiple compatible braille displays are connected ("
                + ", ".join(name for name, _ in candidates)
                + "); select one explicitly"
            )
        return candidates[0][1]

    def connect(self):
        with self._state_lock:
            if self._closing:
                raise RuntimeError("Dotify is shutting down")
        with self._connect_lock:
            return self._connect_locked()

    def _connect_locked(self):
        sink = self._sink
        if sink is not None:
            # Reconnect: reuse the existing sink if the display is already
            # back (its key listener rebinds by itself). Otherwise tear it
            # down and rebuild, which is what a real outage does; the rebuild
            # must re-attach the key listener (_restart_key_listener) or the
            # display keys stay dead for the session.
            try:
                width = sink.connect()
            except Exception:
                self._teardown_sink()
            else:
                return self._commit_connected(width, None)
        if self._requested_profile_id:
            replacement = self._from_profile(profiles.get_profile(self._requested_profile_id))
        else:
            replacement = self._discover()
        # Assign only after a successful connect: an unconnected sink left in
        # self._sink would be reused next time without a key listener.
        width = replacement.connect()
        self._commit_connected(width, replacement)
        if not isinstance(replacement, (HumanWareBluetoothSink, HimsBluetoothSink)):
            self._usb_session = True  # sticky: this session's display is USB
        self._restart_key_listener()
        return width

    def _commit_connected(self, width, replacement):
        """Bind (or keep) a freshly connected sink, unless quit landed
        during the dial; then the fresh handle is closed, not kept."""
        with self._state_lock:
            if not self._closing:
                if replacement is not None:
                    self._sink = replacement
                return width
        if replacement is not None:
            try:
                replacement.close()
            except Exception:
                pass
        else:
            self._teardown_sink()
        raise RuntimeError("Dotify is shutting down")

    def _teardown_sink(self):
        sink, self._sink = self._sink, None
        try:
            sink.close()  # also stops the old sink's key-listener thread
        except Exception:
            pass

    def _restart_key_listener(self):
        """After a rebuild (not a reuse), the new sink has no listener —
        re-attach the remembered callback so display keys survive."""
        if self._keys_changed is None:
            return
        forget_held_chord(self._keys_changed)
        starter = getattr(self._sink, "start_key_listener", None)
        if starter is None:
            print(
                f"[keys:display] display keys are not supported on the "
                f"reconnected {type(self._sink).__name__}; terminal keys "
                "still work",
                file=sys.stderr, flush=True,
            )
            return
        try:
            starter(self._keys_changed)
        except Exception as error:
            print(
                f"[keys:display] could not restart display keys after "
                f"reconnect ({error}); terminal keys still work",
                file=sys.stderr, flush=True,
            )

    def write(self, cells):
        # One read of self._sink: a teardown on another thread can set it to
        # None between a check and the call.
        sink = self._sink
        if sink is None:
            raise RuntimeError("display sink is not connected")
        return sink.write(cells)

    def start_key_listener(self, keys_changed):
        """Forward display-key listening to the detected sink; raises for
        transports that can't deliver key input yet (standard HID, HIMS,
        USB serial) so the caller downgrades to terminal keys with a warning."""
        if self._sink is None:
            raise RuntimeError("display sink is not connected")
        starter = getattr(self._sink, "start_key_listener", None)
        if starter is None:
            raise RuntimeError(
                f"display keys are not supported on {type(self._sink).__name__} yet"
            )
        result = starter(keys_changed)
        # Remembered only after success, so a display-recovery rebuild can
        # re-attach the same callback to the replacement sink.
        self._keys_changed = keys_changed
        return result

    def close(self):
        with self._state_lock:
            self._closing = True
        if self._sink is not None:
            # close() must never raise: quit can land mid-outage.
            self._teardown_sink()
