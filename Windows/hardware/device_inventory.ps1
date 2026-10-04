# Read-only braille-display inventory for support and new-device bring-up.
param([string]$OutputPath)

$ErrorActionPreference = 'Stop'
$Here = Split-Path -Parent $PSCommandPath
$profileCandidates = @(
  (Join-Path $Here 'display_profiles.json'),
  (Join-Path (Split-Path -Parent $Here) 'Text to Braille\display_profiles.json')
)
$ProfileFile = $profileCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $ProfileFile) { throw 'display_profiles.json was not found' }
$registry = Get-Content -LiteralPath $ProfileFile -Raw | ConvertFrom-Json

$pnp = @()
try {
  $present = @(Get-PnpDevice -PresentOnly -ErrorAction Stop)
  foreach ($profile in $registry.profiles) {
    $marker = "VID_$($profile.vid)&PID_$($profile.pid)"
    foreach ($device in $present | Where-Object { $_.InstanceId -like "*$marker*" }) {
      $pnp += [ordered]@{
        profile_id = $profile.id
        profile_name = $profile.name
        transport = $profile.transport
        native_supported = [bool]$profile.native_supported
        status = [string]$device.Status
        class = [string]$device.Class
        friendly_name = [string]$device.FriendlyName
        instance_id = [string]$device.InstanceId
      }
    }
  }
} catch {
  $pnpError = $_.Exception.Message
}

$usbipdState = $null
$usbipdError = $null
$usbipd = Get-Command usbipd -ErrorAction SilentlyContinue
if ($usbipd) {
  try {
    $usbipdState = (& $usbipd.Source state | Out-String) | ConvertFrom-Json
  } catch {
    $usbipdError = $_.Exception.Message
  }
}

$serialPorts = @()
try {
  $serialPorts = @(Get-CimInstance Win32_SerialPort -ErrorAction Stop | ForEach-Object {
    [ordered]@{
      device_id = [string]$_.DeviceID
      name = [string]$_.Name
      pnp_device_id = [string]$_.PNPDeviceID
    }
  })
} catch {
  $serialError = $_.Exception.Message
}

# Paired Bluetooth SPP ports (read-only registry walk, mirroring
# bluetooth_ports.py): shows what name each device paired under and whether a
# registered Bluetooth profile matches it — the first thing to check when a
# display pairs but Dotify does not find it.
$bluetooth = @()
$btError = $null
try {
  $enumRoot = 'HKLM:\SYSTEM\CurrentControlSet\Enum\BTHENUM'
  $sppKeys = @(Get-ChildItem $enumRoot -ErrorAction SilentlyContinue | Where-Object {
    $_.PSChildName.ToLower().StartsWith('{00001101-0000-1000-8000-00805f9b34fb}')
  })
  foreach ($service in $sppKeys) {
    foreach ($instance in @(Get-ChildItem $service.PSPath -ErrorAction SilentlyContinue)) {
      $tail = $instance.PSChildName.Split('&')[-1]
      $address = $tail.Split('_')[0].ToLower()
      # Same gate as bluetooth_ports.py _instance_address: 12 hex digits,
      # nonzero (zero = local listener port, not a paired device).
      if ($address -notmatch '^[0-9a-f]{12}$' -or $address -eq '000000000000') { continue }
      $portName = $null
      try {
        $portName = (Get-ItemProperty -LiteralPath (Join-Path $instance.PSPath 'Device Parameters') -ErrorAction Stop).PortName
      } catch {}
      if (-not $portName) { continue }
      $name = ''
      try {
        $raw = (Get-ItemProperty -LiteralPath ("HKLM:\SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Devices\" + $address) -ErrorAction Stop).Name
        if ($raw) { $name = [Text.Encoding]::UTF8.GetString($raw).Trim([char]0).Trim() }
      } catch {}
      $matched = $null
      # Where-Object drops the lone $null that @() yields when the JSON has
      # no bluetooth_profiles key (an older installed image's registry) —
      # otherwise $prefix.ToLower() below throws and aborts the whole walk.
      foreach ($profile in @($registry.bluetooth_profiles | Where-Object { $_ })) {
        foreach ($prefix in @($profile.name_prefixes)) {
          if ($name -and $name.ToLower().StartsWith($prefix.ToLower())) { $matched = $profile.id; break }
        }
        if ($matched) { break }
      }
      $bluetooth += [ordered]@{
        com_port = [string]$portName
        address = $address
        paired_name = $name
        matched_profile = $matched
      }
    }
  }
} catch {
  $btError = $_.Exception.Message
}

$hidCollections = @()
$hidError = $null
$probeCandidates = @(
  (Join-Path $Here 'hid_probe.py'),
  (Join-Path (Split-Path -Parent $Here) 'Text to Braille\windows_hid.py')
)
$Probe = $probeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
$pythonCandidates = @(
  (Join-Path (Split-Path -Parent $Here) 'runtime\python\python.exe'),
  'python'
)
$Python = $pythonCandidates | Where-Object {
  if ($_ -eq 'python') { [bool](Get-Command python -ErrorAction SilentlyContinue) }
  else { Test-Path -LiteralPath $_ }
} | Select-Object -First 1
if ($Probe -and $Python) {
  try {
    $rawHid = & $Python $Probe inspect --device all --json 2>&1 | Out-String
    if ($LASTEXITCODE -in 0, 2) {
      $hidCollections = ConvertFrom-Json -InputObject $rawHid
    } else {
      $hidError = $rawHid.Trim()
    }
  } catch {
    $hidError = $_.Exception.Message
  }
}

$result = [ordered]@{
  captured_at = [DateTime]::UtcNow.ToString('o')
  computer_architecture = $env:PROCESSOR_ARCHITECTURE
  os = [Environment]::OSVersion.VersionString
  profiles_schema = $registry.schema_version
  matching_pnp_devices = $pnp
  pnp_error = $pnpError
  hid_collections = $hidCollections
  hid_error = $hidError
  usbipd = $usbipdState
  usbipd_error = $usbipdError
  serial_ports = $serialPorts
  serial_error = $serialError
  bluetooth_spp_ports = $bluetooth
  bluetooth_error = $btError
}
$json = $result | ConvertTo-Json -Depth 8
if ($OutputPath) {
  $resolvedParent = Split-Path -Parent ([IO.Path]::GetFullPath($OutputPath))
  if (-not (Test-Path -LiteralPath $resolvedParent)) {
    New-Item -ItemType Directory -Path $resolvedParent -Force | Out-Null
  }
  Set-Content -LiteralPath $OutputPath -Value $json -Encoding utf8
  Write-Host "Wrote read-only device inventory to $OutputPath"
} else {
  $json
}
