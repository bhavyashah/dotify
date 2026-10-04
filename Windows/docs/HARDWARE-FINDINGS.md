# Windows braille-display findings

How Dotify drives braille displays on Windows through the in-box HID and
serial drivers (no WSL, BRLTTY, Zadig, WinUSB, or custom driver), and the
driver-binding dead ends that led there. Read this before adding a display
or revisiting the driver question. [docs/LESSONS.md](../../docs/LESSONS.md)
has the short version.

Reference hardware: Windows 11 ARM64 (build 26200) with a HumanWare NLS
eReader (20 cells, USB `VID_1C71&PID_CE01`).

## The native HID path

### The device

```
USB\VID_1C71&PID_CE01\...            USB Composite Device (usbccgp)
  ├─ MI_00  HID, usage page 0x93 (HumanWare vendor), usage 0x01
  │         reports: out len 24, in len 46, feature len 39
  └─ MI_01  HID, usage page 0x41 (standard HID Braille), usage 0x01
            OUT report id 0x20, 20 cell bytes (out len 21)
            FEAT report id 0x20, usage 0x07, 128 bits
            IN  report id 0x20 (routing/thumb keys, usages 0x201-0x21B + 0x100 array)
```

NVDA's `brailliantB` driver drives PID CE01 over HID with **report id 0x05**
(`HR_BRAILLE`), payload `01 00 <numCells> <cells…>` (module 1 at offset 0), and
reads the cell count from feature report `0x01` (`HR_CAPS`), byte 24. That is
the HumanWare protocol on the `0x93` vendor interface. The `0x41` interface is
the generic HID Braille protocol (report `0x20`).

### Resolution: usbipd's Shared filter

- With `usbipd list` showing the bus as **`Shared`**, both HID collections
  enumerated stably and reported correct cached caps, but every live operation
  (`ReadFile`, `HidD_GetFeature`, `HidD_SetOutputReport`) returned 1167.
- `usbipd unbind --busid 1-1` changed the state to **`Not shared`**. No driver
  changed: the composite parent stayed `usbccgp` and MI_00/MI_01 stayed
  Microsoft `HidUsb`, all with problem code 0.
- After unbinding, NVDA's exact sequence succeeded: usage page `0x93`,
  exclusive `GENERIC_READ|GENERIC_WRITE` handle with `FILE_FLAG_OVERLAPPED`, a
  queued 46-byte input read, a one-second settle, feature report `0x01`, then
  `HidD_SetOutputReport`.
- Capability report `0x01` returned 39 bytes with byte 24 = `0x14` (20 cells).
  Output `05 01 00 14 ff 00 ...` succeeded and the cells alternated full/blank
  under the finger.
- The staged runtime then ran bundled liblouis grade 2 through the native sink
  to the physical display.

The launcher detects a shared display through `usbipd state` (a
`StubInstanceId` on a registered profile's VID/PID) and requests one elevated
`unbind` for that exact bus. It never touches unrelated USB devices.

One device quirk matters: the first collection-open cycle after USB mode is
selected can expose descriptors while live I/O still returns 1167. Two short
shared enumeration/open passes across the collections reliably wake the path;
the production sink performs those passes before opening its single exclusive
NVDA-style session. HID paths are enumerated fresh every time.

### Standard HID Braille (usage page `0x41`)

The `standard-hid` sink is descriptor-driven: it reads Windows HID value
capabilities for the report ID, link collection, braille-cell usage, bit width,
row count, and cell count, then uses `HidP_SetUsageValueArray` and overlapped
`WriteFile`. Multi-row and irregular layouts are rejected explicitly.

On the eReader, the value caps correctly report usage page `0x41`, report
`0x20`, link collection 3, six-dot cell usage `0x04`, 8-bit values, and 20
cells, and `HidP_SetUsageValueArray` builds the expected 21-byte report — but
live `WriteFile` on that collection still returns 1167. The eReader therefore
stays on its `0x93` HumanWare path; promoting `standard-hid` to verified needs
a different, standards-compliant display.

## liblouis grade 2 on Windows

`core/text-to-braille/braille_engine/translator.py` → `LiblouisDllTranslator`
(ctypes over the bundled `liblouis.dll`, liblouis 3.38.0 win64). It is
cell-for-cell identical to WSL liblouis 3.20.0 on a committed reference set
(`Windows/installer/vendor/reference_g2.json`), e.g. "knowledge" → 1 cell. The
DLL's `lou_charSize()` is 4 (a UTF-32 build), so the translator encodes
UTF-32-LE. It is auto-selected on Windows when the `louis` Python module is
absent.

## BrlAPI from Windows Python

`core/text-to-braille/braille_engine/sinks/brlapi_dll.py` → `DllConnection`,
used by `BrlapiSink` when `import brlapi` fails. It connects, reads
`driverName` and `displaySize`, and enters global TTY mode, but two things are
required:

1. **Run BRLTTY as your user, not as the auto-started Windows service.** The
   service runs as `SYSTEM` and creates its named pipe `\\.\pipe\BrlAPI0` with
   a SYSTEM-only ACL; a user-level client gets `PermissionError 13` opening it.
   `brltty.exe -n -A Auth=none` run as the user creates a pipe the user can
   open.
2. Connect with **`brlapi.Connection(host=b":0")`** (or the ctypes equivalent)
   so the client uses the local named pipe.

Even then `displaySize` comes back `(0,0)` / `driverName=NoBraille`, because
that BRLTTY still cannot claim the USB display (next section).

## Dead ends: BRLTTY/libusb driver binding

BRLTTY for Windows can only talk to this HID device through **libusb**, which
requires a WinUSB (or libusb0) driver bound to the device. The device ships
bound to `HidUsb`. Every rebinding attempt failed, each for a different reason.

### Attempt A — BRLTTY's own libusb0 filter driver

- Installed BRLTTY for Windows 6.9.1 (both the `libusb-1.0` and legacy
  `libusb-win32` variants). The service registers as `BrlAPI`.
- `pnputil /add-driver brltty-libusb.inf /install` → **"Failed to add driver
  package: The third-party INF does not contain digital signature
  information."**
- The legacy `libusb0.sys` kernel driver is x64-only, and **kernel-mode drivers
  are not emulated on Windows on ARM**, so it would not load here even if
  signed.
- With the eReader still on `HidUsb`, `brltty -b hw -d hid:` logged
  **`hidOpenUSBDevice error 40: Function not implemented`** and
  **`libusb_open error 40`**: libusb has no accessible driver for it.

### Attempt B — usbipd force-bind

- `usbipd bind --force --busid 1-1` binds usbipd's VBoxUSB stub driver.
- `libusb_open_device_with_vid_pid(0x1c71, 0xce01)` then returned NULL:
  libusb-1.0 cannot use the VBoxUSB stub.

### Attempt C — Zadig, driven programmatically

Zadig's raw Win32 GUI is inaccessible to NVDA, so it was driven with window
messages. It selected MI_00, read back USB ID `1C71`/`CE01`/`00` and target
driver WinUSB, and clicked **Replace Driver**. libwdi then extracted the WinUSB
files, created and self-signed a catalog, added its certificate to `Root` and
`TrustedPublisher`, and staged the package with `SetupCopyOEMInf`.

**It never bound.** The installer log showed `device_id: ''` /
`hardware_id: ''`: Zadig built a generic `MS_COMP_WINUSB` package with no real
hardware ID, because synthetic Win32 selection does not populate Zadig's
internal `wdi_device_info`. Zadig needs a genuine mouse selection and cannot be
reliably automated blind.

### Attempt D — hand-authored, self-signed WinUSB INF

A WinUSB INF targeting exactly `USB\VID_1C71&PID_CE01&MI_00` (referencing the
in-box `winusb.inf`, so no third-party kernel code), a catalog from
`New-FileCatalog`, signed with `Set-AuthenticodeSignature` and a self-signed
code-signing certificate imported into `LocalMachine\Root` and
`TrustedPublisher`:

- `pnputil /add-driver … /install` → **"A certificate chain processed, but
  terminated in a root certificate which is not trusted by the trust
  provider."**, even with the certificate in `LocalMachine\Root`.
- The SetupAPI fallback (`SetupCopyOEMInfW` + `UpdateDriverForPlugAndPlayDevices`
  with `INSTALLFLAG_FORCE`) failed with **`0x800B0100`
  (TRUST_E_NOSIGNATURE)**.

Windows 11 refuses self-signed driver catalogs; it wants attestation/WHQL
signing. That is why Zadig ships pre-attested WinUSB binaries. Do not
distribute a self-signed driver.

## Display support beyond the eReader

Upstream NVDA is the protocol reference:

- [HumanWare Brailliant/BrailleNote driver](https://github.com/nvaccess/nvda/blob/master/source/brailleDisplayDrivers/brailliantB.py)
- [HIMS driver](https://github.com/nvaccess/nvda/blob/master/source/brailleDisplayDrivers/hims.py)

`hardware/display_profiles.json` is the one registry shared by the runtime,
diagnostics, staging, and the launcher. Native support is added only where
NVDA's source gives exact in-box HID or COM behavior:

- all current NVDA HumanWare HID identities (NLS eReader, Mantis Q40,
  Chameleon 20, Brailliant BI 20X/40X, BrailleOne, legacy Brailliant BI/B HID,
  BI 14 HID, BrailleNote Touch and Touch v2/Plus), sharing the report `0x05`
  implementation proven on the eReader;
- HumanWare USB serial (legacy BI 32/40/80 and BI 14): exact VID/PID COM
  discovery, 115200 baud, even parity, cell count from the initialization
  response;
- HIMS Braille Edge 3S HID: width from feature report `0x01` byte 9, the
  upstream length-plus-cells output report;
- HIMS SyncBraille and Braille Edge 2S USB serial: checksummed HIMS packet
  framing and validated cell-count responses;
- any descriptor-valid, single-row standard HID Braille display (`0x41`);
- HumanWare/APH and HIMS families over Bluetooth SPP, matched by paired name.

Serial enumeration is exact VID/PID matching, so unrelated COM ports are never
probed, and multiple candidate displays fail closed. Only the NLS eReader and
the Brailliant BI 40X are hardware-verified; every other identity is
implemented from NVDA's source and untested on hardware.

Notes per family:

- **Brailliant BI 20X** (`1C71:C141`) uses usage page `0x93` and the same
  report family as the eReader.
- **BrailleNote Touch** (`1C71:C00A`) and **Touch v2/Plus** (`1C71:C00E`) are
  handled by the same NVDA driver. The device must be running its Braille
  Terminal app with USB selected. Validate the two generations separately, and
  record whether Windows installs any vendor component on first connection
  before calling either driver-free.
- **BrailleSense** is a multi-generation family, not one PID. NVDA's older USB
  identity `045E:930A` (and Braille EDGE 40 `045E:930B`) uses custom HIMS bulk
  USB, which needs a compatible signed Windows driver; both are registered as
  discovery-only. `hims_protocol.py` already implements and tests the HIMS
  display and cell-count packet framing. To add a BrailleSense model: put the
  exact unit in "Terminal for Screen Reader" USB mode, capture
  `device_inventory.ps1` output without changing its driver, determine whether
  it exposes standard braille HID, HIMS HID, USB serial, or HIMS bulk, and
  prefer an in-box HID or serial path. Treat every untested generation as
  unsupported, even if it shares a marketing name.

BRLTTY on Linux still covers far more legacy and vendor-specific hardware,
because it can reach USB bulk endpoints without a Windows kernel driver. Native
Windows cannot claim device-for-device parity while custom-bulk devices require
a signed driver.

## Per-device acceptance checklist

Before calling a display "verified" (`hardware_verified` in
`display_profiles.json`), check:

1. Record the exact model, firmware, VID/PID, interface, usage page,
   input/output/feature report lengths, and device-side terminal-mode steps.
2. Test on clean Windows 11 x64 and on Windows 11 ARM64.
3. Confirm installation and operation need no WSL or BRLTTY.
4. Confirm driver state: HID targets must use an in-box signed driver with no
   Zadig or manual driver step.
5. Run the blank, full, alternating, and walking-dot probe patterns.
6. Confirm the descriptor-reported cell width; malformed lengths must be
   rejected safely.
7. Run file → grade-2 braille and live speech → grade-2 braille.
8. Physically verify the backlog thermometer boundaries, the cell-2 source
   marker, and the catch-up reset (`thermometer_probe.py`).
9. Verify the panel controls and the display's own keys: speed, grade, reading
   mode, catch up, Human mode, and quit.
10. Run for at least 30 minutes without dropped writes or collection flicker.
11. Unplug and replug during a session; reconnect must be bounded, use a fresh
    HID path, and not corrupt text.
12. With NVDA or JAWS already owning the display, Dotify must say so in the
    panel and connect on its own once the display is freed.
13. Verify usbipd `Not shared`, `Shared`, and `Attached` states without
    touching any unrelated USB device.
14. Rebuild the installer and rerun `verify.ps1`.
