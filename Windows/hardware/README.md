# Native Windows display diagnostics

These tools are read-only unless the explicit `write` subcommand is used. They
use Windows' in-box APIs and never install or change a driver.

## Inventory first

For a known or new display in its USB terminal/screen-reader mode:

```powershell
powershell -ExecutionPolicy Bypass -File `
  ".\Windows\hardware\device_inventory.ps1"
```

The JSON output records matching profile/PnP identities, HID collections and
value capabilities, usbipd state, architecture, serial ports, and paired
Bluetooth SPP ports (`bluetooth_spp_ports`: COM port, address, paired name,
matched profile). To save it:

```powershell
...\device_inventory.ps1 -OutputPath .\display-inventory.json
```

Review the file before sharing it because Windows instance IDs and Bluetooth
paired names may contain a device serial number.

`display_profiles.json` is the single display identity registry used by the
native sink, diagnostic tool, staged image, and launcher. `profiles` entries
are USB identities (VID/PID); `bluetooth_profiles` entries are paired-name
prefix families for Bluetooth SPP, discovered read-only from the registry by
`bluetooth_ports.py` (no port is opened during discovery — opening a
Bluetooth COM port is what dials the radio). `native_supported` means the
software path is implemented; `hardware_verified` is a separate claim.

## Bluetooth probes

`bluetooth_probe.py` drives the same Bluetooth sinks the appliance uses.
`list` is read-only; `write`/`keys` open the port, which dials the radio
link. Run it with a Python that has pyserial (the staged runtime does):

```powershell
python ".\Windows\hardware\bluetooth_probe.py" list
python ".\Windows\hardware\bluetooth_probe.py" write --pattern alternating
python ".\Windows\hardware\bluetooth_probe.py" keys --seconds 30
```

## HumanWare HID probes

`hid_probe.py` follows NVDA's Brailliant driver: exclusive overlapped handle,
pending input read, feature report `0x01` for cell count, then output report
`0x05` through `HidD_SetOutputReport`.

```powershell
python ".\Windows\hardware\hid_probe.py" inspect --device all
python ".\Windows\hardware\hid_probe.py" probe --device nls-ereader
python ".\Windows\hardware\hid_probe.py" write `
  --device nls-ereader --pattern alternating
```

Valid HumanWare device choices are printed by `--help`. BI 20X and BrailleNote
profiles are protocol-implemented but should not be written until the exact
unit is available and its terminal mode is active.

Useful one-variable diagnostics:

```powershell
# Change handle sharing only.
python ".\Windows\hardware\hid_probe.py" probe --share shared

# Change opening mode only.
python ".\Windows\hardware\hid_probe.py" probe --io synchronous

# Exercise a standard usage-page 0x41 collection with descriptor-built output.
python ".\Windows\hardware\hid_probe.py" write `
  --device nls-ereader --protocol standard --transport writefile

# Bypass only HumanWare cell-count feature report; descriptor must agree.
python ".\Windows\hardware\hid_probe.py" write --cell-count 20
```

The NLS eReader's standard `0x41` collection descriptors parse correctly, but
live `WriteFile` still returns error 1167 on the tested unit. Its verified path
remains HumanWare usage page `0x93`.

`inspect` exits 0 for a stable nonempty set, 2 when no matching collection is
present, and 3 when the set changes between snapshots.

After a normal HumanWare connection is verified, the timed physical status-cell
sequence is:

```powershell
python ".\Windows\hardware\thermometer_probe.py" `
  --display nls-ereader --hold-seconds 4
```

It presents the five documented backlog levels plus the cleared state, keeps
cell 2 blank (in the app cell 2 carries the `s`/`h` source marker), and
clears the display at exit.

## HIMS and BrailleSense

`hims_protocol.py` is the HIMS display and cell-count packet codec, used by
the HIMS serial and Bluetooth sinks. There is no Windows backend for HIMS
custom-bulk USB devices: the `045E:930A` BrailleSense path is custom USB
bulk, and a safe distributable
backend depends on the exact model exposing a compatible signed driver and
known endpoints. Run `device_inventory.ps1` on the target unit before adding
any backend; do not substitute a self-signed WinUSB driver.
