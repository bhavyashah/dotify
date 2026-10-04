# Windows release validation: test suites, stage build with its secrets gate,
# and smoke checks against the staged image. Never modifies the core/ trees.
param(
  [switch]$SkipWsl,
  [switch]$Hardware,
  [switch]$Installer,
  [switch]$RequireSignature
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Here = Split-Path -Parent $PSCommandPath        # Windows\installer
$Platform = Split-Path -Parent $Here             # Windows
$RepoRoot = Split-Path -Parent $Platform         # repo root
$Braille = Join-Path $RepoRoot 'core\text-to-braille'
$Stage = Join-Path $Here 'stage'
$results = @()

function Invoke-Checked([string]$Name, [scriptblock]$Command) {
  Write-Host "`n== $Name ==" -ForegroundColor Cyan
  & $Command
  if ($LASTEXITCODE -ne 0) { throw "$Name failed with exit code $LASTEXITCODE" }
  $script:results += $Name
}

Invoke-Checked 'Windows braille tests' {
  Push-Location $Braille
  try { python -m pytest -q } finally { Pop-Location }
}

Invoke-Checked 'Windows overlay tests' {
  Push-Location $RepoRoot
  try { python -m pytest -q 'Windows/hardware' 'Windows/overlay' }
  finally { Pop-Location }
}

if (-not $SkipWsl) {
  $drive = $Braille.Substring(0, 1).ToLowerInvariant()
  $rest = $Braille.Substring(2).Replace('\', '/')
  $wslBraille = "/mnt/$drive$rest"
  Invoke-Checked 'WSL braille tests' {
    wsl.exe -d Ubuntu-22.04 --cd $wslBraille -- python3 -m pytest -q
  }
}

$buildArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $Here 'build.ps1'))
if ($Installer) { $buildArgs += '-Installer' }
Invoke-Checked 'Stage build and secrets gate' { powershell.exe @buildArgs }

$bad = @(Get-ChildItem -LiteralPath $Stage -Recurse -Force -File | Where-Object {
  $_.Name -eq '.env' -or $_.Extension -eq '.key'
})
$bad += @(Get-ChildItem -LiteralPath $Stage -Recurse -Force -Directory | Where-Object {
  $_.Name -eq 'speakers'
})
if ($bad.Count -gt 0) { throw "Post-build secrets scan failed: $($bad.FullName -join '; ')" }
$results += 'Independent post-build secrets scan'

$stagedIndex = Get-Content -LiteralPath `
  (Join-Path $Stage 'Speech to text\public\index.html') -Raw
if ($stagedIndex -notmatch 'dotify-controls-loader\.js' -or
    $stagedIndex -notmatch 'windows-speech-overlay\.js') {
  throw 'Staged transcription page is missing a Windows appliance overlay'
}
foreach ($relative in @(
  'Speech to text\public\dotify-controls-loader.js',
  'Speech to text\public\windows-speech-overlay.js',
  # The panel's News article / Novel play-on-display sources fetch these;
  # missing means the button errors on an installed machine.
  'Speech to text\public\texts\news-article.txt',
  'Speech to text\public\texts\novel.txt',
  'Text to Braille\control_bridge.py',
  'Text to Braille\appliance_controls.js',
  'Text to Braille\local_speech_server.py',
  'Text to Braille\auto_display_sink.py',
  # Some of these are imported lazily, so the import smoke below would not
  # notice them missing; windows_run.py is what the launcher runs.
  'Text to Braille\display_cold_watch.py',
  'Text to Braille\hims_hid_sink.py',
  'Text to Braille\serial_sinks.py',
  'Text to Braille\sink_reconnect.py',
  'Text to Braille\windows_run.py',
  'Text to Braille\display_profiles.json',
  'Text to Braille\bluetooth_ports.py',
  # The offline-model contract: the manifest drives both the server's
  # download manager and the decode service's ready check. The launcher
  # passes the staged manifest path — missing means no offline engine ever.
  'Speech to text\offline-model.js',
  'Speech to text\offline-model.json'
)) {
  if (-not (Test-Path -LiteralPath (Join-Path $Stage $relative))) {
    throw "Staged appliance UI file is missing: $relative"
  }
}
$shippingIndex = Get-Content -LiteralPath `
  (Join-Path $RepoRoot 'core\speech-to-text\public\index.html') -Raw
if ($shippingIndex -match 'dotify-controls-loader\.js') {
  throw 'Shipping speech application was modified instead of using the Windows overlay'
}
$results += 'Staged appliance UI overlay and shipping-source isolation'

Invoke-Checked 'Staged native display dependencies' {
  & (Join-Path $Stage 'runtime\python\python.exe') -c `
    'import serial, auto_display_sink, bluetooth_ports; print(serial.__version__)'
}

Invoke-Checked 'Staged offline speech dependencies' {
  # sherpa-onnx must import under the STAGED embeddable interpreter (the
  # wheel is ABI-tagged; a host-python mismatch dies exactly here), and the
  # decode service must parse the staged manifest the launcher hands it.
  # local_speech_server imports via the ._pth "Text to Braille" entry.
  $manifest = (Join-Path $Stage 'Speech to text\offline-model.json') -replace '\\', '/'
  & (Join-Path $Stage 'runtime\python\python.exe') -c `
    "import sherpa_onnx, pathlib, local_speech_server as s; files = s.files_from_manifest(pathlib.Path('$manifest')); assert len(files) == 4, files; print(sherpa_onnx.__version__)"
}

Invoke-Checked 'Staged arm64 offline decode runtime' {
  # The launcher picks runtime\python-arm64 on Windows-on-ARM by Test-Path
  # alone, so the layout must be complete on every build; the import proof
  # itself can only run where ARM64 binaries execute.
  $armPython = Join-Path $Stage 'runtime\python-arm64\python.exe'
  if (-not (Test-Path $armPython)) { throw 'runtime\python-arm64\python.exe is missing' }
  if (-not (Test-Path (Join-Path $Stage 'runtime\pylibs-arm64\sherpa_onnx'))) {
    throw 'runtime\pylibs-arm64\sherpa_onnx is missing'
  }
  if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') {
    $manifest = (Join-Path $Stage 'Speech to text\offline-model.json') -replace '\\', '/'
    & $armPython -c `
      "import sherpa_onnx, pathlib, local_speech_server as s; files = s.files_from_manifest(pathlib.Path('$manifest')); assert len(files) == 4, files; print('native arm64 sherpa', sherpa_onnx.__version__)"
  } else {
    Write-Host 'arm64 layout present (the import proof needs an ARM64 machine)'
  }
}

Invoke-Checked 'Staged speaker-identification dependencies' {
  # matcher/fbank import via the ._pth speaker-id entry; numpy/onnxruntime
  # via pylibs — one line proves all four wirings.
  & (Join-Path $Stage 'runtime\python\python.exe') -c `
    'import numpy, onnxruntime, matcher, fbank; print(onnxruntime.__version__)'
}
$speakerModelDir = Join-Path $Stage 'Speech to text\speaker-id\models'
if (-not (Test-Path -LiteralPath (Join-Path $speakerModelDir 'resnet34_voxceleb_lm.onnx')) -or
    -not (Test-Path -LiteralPath (Join-Path $speakerModelDir 'model.json'))) {
  throw 'Bundled speaker-embedding model is missing or incomplete'
}
$results += 'Bundled speaker-embedding model'

Invoke-Checked 'Staged speech server tests' {
  Push-Location (Join-Path $Stage 'Speech to text')
  try { & (Join-Path $Stage 'runtime\node\node.exe') --test }
  finally { Pop-Location }
}

Invoke-Checked 'Staged launcher simulation' {
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Stage 'launcher.ps1') `
    -Sim -NoBrowser -PaceMs 1
}

Invoke-Checked 'Read-only device inventory' {
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
    (Join-Path $Stage 'diagnostics\device_inventory.ps1') | Out-Null
}

if ($Hardware) {
  Invoke-Checked 'Native HID descriptor inspection' {
    & (Join-Path $Stage 'runtime\python\python.exe') `
      (Join-Path $Stage 'Text to Braille\windows_hid.py') inspect `
      --device nls-ereader --snapshots 2
  }
  Invoke-Checked 'Native HID file-to-grade2 hardware smoke' {
    & (Join-Path $Stage 'runtime\python\python.exe') `
      (Join-Path $Stage 'Text to Braille\windows_run.py') `
      --file (Join-Path $Stage 'Text to Braille\sample.txt') `
      --sink native-hid --display nls-ereader --pace 1
  }
}

if ($Installer) {
  $installerPath = Join-Path $Here 'Output\DotifySetup.exe'
  if (-not (Test-Path -LiteralPath $installerPath)) { throw 'Installer output is missing' }
  $signature = Get-AuthenticodeSignature -LiteralPath $installerPath
  if ($RequireSignature -and $signature.Status -ne 'Valid') {
    throw "Installer signature is $($signature.Status), not Valid"
  }
  $hash = Get-FileHash -LiteralPath $installerPath -Algorithm SHA256
  Write-Host "Installer signature: $($signature.Status)"
  Write-Host "Installer SHA256: $($hash.Hash)"
  $results += 'Installer artifact and Authenticode status'
}

Write-Host "`nPASS: $($results.Count) validation groups" -ForegroundColor Green
$results | ForEach-Object { Write-Host "  - $_" }
