# Assembles the Windows install image into stage\ from the repo + pinned vendors.
param(
  [switch]$Installer,  # also compile installer.iss (needs ISCC)
  [switch]$Sign,       # Authenticode-sign the compiled installer from a cert store
  [string]$CertificateThumbprint = $env:DOTIFY_SIGNING_CERT_THUMBPRINT,
  [ValidateSet('CurrentUser', 'LocalMachine')]
  [string]$CertificateStoreLocation = 'CurrentUser',
  [string]$TimestampUrl = 'http://timestamp.digicert.com'
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Here  = Split-Path -Parent $PSCommandPath      # Windows\installer
$Platform = Split-Path -Parent $Here            # Windows
$Repo  = Split-Path -Parent $Platform           # repo root
$Stage = Join-Path $Here 'stage'
$Vendor = Join-Path $Here 'vendor'
$Lock = Get-Content (Join-Path $Here 'vendor.lock.json') -Raw | ConvertFrom-Json
if ($Sign -and -not $Installer) { throw '-Sign requires -Installer' }

function Fetch($item) {
  $dest = Join-Path $Vendor $item.file
  if (-not (Test-Path $dest)) { Invoke-WebRequest -Uri $item.url -OutFile $dest }
  $h = (Get-FileHash $dest -Algorithm SHA256).Hash.ToLower()
  if ($h -ne $item.sha256.ToLower()) { throw "hash mismatch: $($item.file)" }
  return $dest
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
function Unzip($zip, $dest) {
  [System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $dest)
}

# /R:1 /W:1: robocopy's default is a million retries, 30 s apart - a single
# locked file would hang the build for weeks. Fail fast instead.
function Copy-Tree($src, $dst, [string[]]$xd, [string[]]$xf) {
  $cmd = @($src, $dst, '/E', '/R:1', '/W:1', '/NFL', '/NDL', '/NP')
  if ($xd) { $cmd += '/XD'; $cmd += $xd }
  if ($xf) { $cmd += '/XF'; $cmd += $xf }
  robocopy @cmd | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($src)" }
  $global:LASTEXITCODE = 0
}

if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
New-Item -ItemType Directory -Force "$Stage\runtime" | Out-Null

# App folders - exclude runtime junk and anything secret-shaped. speaker-id's
# models\ is excluded because the model is vendored below with a pinned hash
# (a dev checkout may hold an unpinned download there). Internal plans (docs\)
# and the speaker-id pytest suite are dev-only and must not reach end-user
# machines — but tests\ (the node --test suite) deliberately STAYS staged:
# verify.ps1's "Staged speech server tests" runs it from the installed layout.
Copy-Tree "$Repo\core\speech-to-text" "$Stage\Speech to text" `
  -xd node_modules, models, '__pycache__', 'docs', 'recordings', "$Repo\core\speech-to-text\speaker-id\tests" `
  -xf '.env', '*.key', 'server.log', 'transcript.txt', '.gitignore', `
      '*.wav', '*.ref.txt', '*.hyp.txt'
# Logs may hold transcribed personal speech; docs\ and the test suite are
# dev-only.
Copy-Tree "$Repo\core\text-to-braille" "$Stage\Text to Braille" `
  -xd '__pycache__', '.pytest_cache', '.pytmp', 'docs', 'tests' `
  -xf '.env', '*.key', '*.log', 'transcript.txt', `
      'pytest.ini', 'conftest.py', `
      'grade_demo.txt', '.gitignore', '.gitattributes'

# Windows-only native display overlay. The validated shipping app folders remain
# untouched; these files exist only in the staged Windows image.
Copy-Item "$Platform\hardware\hid_probe.py" "$Stage\Text to Braille\windows_hid.py"
Copy-Item "$Platform\hardware\display_profiles.py" "$Stage\Text to Braille\display_profiles.py"
Copy-Item "$Platform\hardware\display_profiles.json" "$Stage\Text to Braille\display_profiles.json"
Copy-Item "$Platform\hardware\hims_protocol.py" "$Stage\Text to Braille\hims_protocol.py"
Copy-Item "$Platform\hardware\bluetooth_ports.py" "$Stage\Text to Braille\bluetooth_ports.py"
Copy-Item "$Platform\overlay\native_hid_sink.py" "$Stage\Text to Braille\native_hid_sink.py"
Copy-Item "$Platform\overlay\standard_hid_sink.py" "$Stage\Text to Braille\standard_hid_sink.py"
Copy-Item "$Platform\overlay\hims_hid_sink.py" "$Stage\Text to Braille\hims_hid_sink.py"
Copy-Item "$Platform\overlay\serial_sinks.py" "$Stage\Text to Braille\serial_sinks.py"
Copy-Item "$Platform\overlay\sink_reconnect.py" "$Stage\Text to Braille\sink_reconnect.py"
Copy-Item "$Platform\overlay\auto_display_sink.py" "$Stage\Text to Braille\auto_display_sink.py"
Copy-Item "$Platform\overlay\display_cold_watch.py" "$Stage\Text to Braille\display_cold_watch.py"
Copy-Item "$Platform\overlay\control_bridge.py" "$Stage\Text to Braille\control_bridge.py"
Copy-Item "$Platform\overlay\appliance_controls.js" "$Stage\Text to Braille\appliance_controls.js"
Copy-Item "$Platform\overlay\local_speech_server.py" "$Stage\Text to Braille\local_speech_server.py"
Copy-Item "$Platform\overlay\windows_run.py" "$Stage\Text to Braille\windows_run.py"

# Add the Windows appliance controls to the staged speech page.  The shipping
# Speech to text source remains byte-for-byte untouched in the repository.
Copy-Item "$Platform\overlay\speech_ui\dotify-controls-loader.js" `
  "$Stage\Speech to text\public\dotify-controls-loader.js"
Copy-Item "$Platform\overlay\speech_ui\windows-speech-overlay.js" `
  "$Stage\Speech to text\public\windows-speech-overlay.js"
# Bundled play-on-display texts (the panel's News article / Novel sources).
# Windows-only reading material: it lives under the platform overlay, never
# in the shipping core page.
New-Item -ItemType Directory -Force "$Stage\Speech to text\public\texts" | Out-Null
Copy-Item "$Platform\overlay\texts\news-article.txt" "$Stage\Speech to text\public\texts\news-article.txt"
Copy-Item "$Platform\overlay\texts\novel.txt" "$Stage\Speech to text\public\texts\novel.txt"
$speechIndex = "$Stage\Speech to text\public\index.html"
$speechHtml = [IO.File]::ReadAllText($speechIndex)
if ($speechHtml -notmatch '</body>') { throw 'speech index has no </body> injection point' }
# Idempotent: re-running this against an already-injected page must not add
# a second overlay (two overlays = two mic captures and every
# utterance posted twice into braille; the JS carries its own guard too).
if ($speechHtml -notmatch 'windows-speech-overlay\.js') {
  # No favicon injection: the core page ships its own brand icon
  # (public\brand\favicon.svg).
  $speechHtml = $speechHtml.Replace(
    '</body>',
    '<script src="/windows-speech-overlay.js"></script>' + [Environment]::NewLine +
    '<script src="/dotify-controls-loader.js"></script>' + [Environment]::NewLine + '</body>'
  )
  [IO.File]::WriteAllText($speechIndex, $speechHtml, [Text.UTF8Encoding]::new($false))
}
# Brand kit app icon at the app root: installer.iss points every shortcut and
# the Add/Remove Programs entry at {app}\Dotify.ico.
Copy-Item "$Here\assets\Dotify.ico" "$Stage\Dotify.ico"

New-Item -ItemType Directory -Force "$Stage\diagnostics" | Out-Null
Copy-Item "$Platform\hardware\device_inventory.ps1" "$Stage\diagnostics\device_inventory.ps1"
Copy-Item "$Platform\hardware\thermometer_probe.py" "$Stage\diagnostics\thermometer_probe.py"

# Runtime: embeddable Python + pylibs (websockets) + node.exe + ws module.
Unzip (Fetch $Lock.python) "$Stage\runtime\python"
# The wheels pip resolves below are ABI-tagged for the python RUNNING pip,
# but they execute under the staged embeddable interpreter. A mismatched
# PATH python (common on end-user first runs) builds a stage whose
# sherpa-onnx/numpy/onnxruntime imports die at runtime with only hidden-log
# warnings - gate on a matching major.minor before installing.
if ($Lock.python.url -notmatch 'python-(\d+)\.(\d+)\.[\d]+-embed') {
  throw "cannot parse the pinned embeddable Python version from '$($Lock.python.url)'"
}
$pinnedPython = "$($Matches[1]).$($Matches[2])"
$hostPython = (cmd /c "python -c ""import sys; print('%d.%d' % sys.version_info[:2])"" 2>&1" | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
  throw "Python was not found on PATH (needed to assemble the packaged dependencies): $hostPython"
}
if ($hostPython -ne $pinnedPython) {
  throw ("the python on PATH is $hostPython but the staged embeddable Python is $pinnedPython - " +
    "pip would fetch $hostPython wheels the staged interpreter cannot import. " +
    "Install Python $pinnedPython (or put it first on PATH) and rebuild.")
}
# cmd /c merges pip's stderr (resolver notices) so PS5.1 + EAP Stop doesn't
# promote harmless warnings to terminating errors; the exit code still gates.
cmd /c "python -m pip install --target ""$Stage\runtime\pylibs"" $($Lock.websockets.pin) $($Lock.pyserial.pin) $($Lock.sherpa_onnx.pin) $($Lock.numpy.pin) $($Lock.onnxruntime.pin) --quiet 2>&1"
if ($LASTEXITCODE -ne 0) { throw 'pip install packaged Python dependencies failed' }
# The ._pth file pins sys.path exactly (embeddable python runs isolated: no
# script-dir insertion, no PYTHONPATH) - add our lib target and the app folder.
$pth = Get-ChildItem "$Stage\runtime\python\python3*._pth" | Select-Object -First 1
Add-Content $pth.FullName "..\pylibs"
Add-Content $pth.FullName "..\..\Text to Braille"
Add-Content $pth.FullName "..\..\Speech to text\speaker-id"
# Native ARM64 decode runtime, ADDITIVE next to the shared x64 one (the
# stage\runtime\python + pylibs layout is a compatibility contract). On
# Windows-on-ARM machines the x64 runtime above emulates at ~4x the decode
# cost (measured RTF 0.81 emulated vs 0.19 native on a Snapdragon X Elite),
# which leaves offline captions no headroom. Only the offline decode service runs on
# this interpreter (the launcher picks it per machine); ticker/speaker-id
# stay on the shared x64 runtime, so only the decode service's imports are
# installed here. sherpa's win_arm64 wheels come hash-pinned from k2-fsa's
# own index (PyPI has none); websockets resolves to its pure wheel and
# numpy to its win_arm64 build via pip's cross-platform mode.
Unzip (Fetch $Lock.python_arm64) "$Stage\runtime\python-arm64"
$sherpaArm = Fetch $Lock.sherpa_onnx_arm64
$sherpaCoreArm = Fetch $Lock.sherpa_onnx_core_arm64
cmd /c "python -m pip install --target ""$Stage\runtime\pylibs-arm64"" --platform win_arm64 --python-version $pinnedPython --implementation cp --only-binary=:all: --no-deps ""$sherpaArm"" ""$sherpaCoreArm"" $($Lock.websockets.pin) $($Lock.numpy.pin) --quiet 2>&1"
if ($LASTEXITCODE -ne 0) { throw 'pip install arm64 decode dependencies failed' }
$pthArm = Get-ChildItem "$Stage\runtime\python-arm64\python3*._pth" | Select-Object -First 1
Add-Content $pthArm.FullName "..\pylibs-arm64"
Add-Content $pthArm.FullName "..\..\Text to Braille"

# Wheel test suites are megabytes the runtime never imports (numpy alone ships
# ~14 MB of tests), and pip's console-script shims in bin\ target the build
# machine's interpreter - neither belongs in the installed image. Deepest
# first so a nested tests\ is not deleted twice.
$wheelTests = Get-ChildItem "$Stage\runtime" -Recurse -Directory -Filter tests |
  Sort-Object { $_.FullName.Length } -Descending
foreach ($dir in $wheelTests) {
  if (Test-Path $dir.FullName) { Remove-Item $dir.FullName -Recurse -Force }
}
if (Test-Path "$Stage\runtime\pylibs\bin") { Remove-Item "$Stage\runtime\pylibs\bin" -Recurse -Force }
if (Test-Path "$Stage\runtime\pylibs-arm64\bin") { Remove-Item "$Stage\runtime\pylibs-arm64\bin" -Recurse -Force }
if (Test-Path "$Vendor\node-tmp") { Remove-Item "$Vendor\node-tmp" -Recurse -Force }
Unzip (Fetch $Lock.node) "$Vendor\node-tmp"
$nodeExe = Get-ChildItem "$Vendor\node-tmp" -Recurse -Filter node.exe | Select-Object -First 1
New-Item -ItemType Directory -Force "$Stage\runtime\node" | Out-Null
Copy-Item $nodeExe.FullName "$Stage\runtime\node\node.exe"
Remove-Item "$Vendor\node-tmp" -Recurse -Force
# ws is the server's only runtime npm dep (zero transitive deps) - install pinned.
# --omit=dev keeps devDependencies (playwright, a browser-test tool the server
# never imports) out of the shipped image; without it npm reifies the whole
# package.json tree and ~17 MB of Playwright rides along.
npm install --prefix "$Stage\Speech to text" $Lock.ws.pin --no-save --ignore-scripts --omit=dev --loglevel error
if ($LASTEXITCODE -ne 0) { throw 'npm install ws failed' }

# liblouis: dll + tables (re-fetch verifies hash; unzip if not already).
Fetch $Lock.liblouis | Out-Null
if (-not (Test-Path "$Vendor\liblouis\bin\liblouis.dll")) {
  Unzip "$Vendor\$($Lock.liblouis.file)" "$Vendor\liblouis"
}
Copy-Tree "$Vendor\liblouis" "$Stage\vendor\liblouis"
# The runtime loads bin\liblouis.dll and reads share\liblouis\tables
# (translator.py). The lou_*.exe CLI tools, import libraries, headers, and
# GNU info pages are build-machine artifacts (~4.8 MB) no shipped code runs.
Get-ChildItem "$Stage\vendor\liblouis\bin" -Filter 'lou_*.exe' | Remove-Item -Force
foreach ($extra in 'bin\liblouis-20.def', 'lib', 'include', 'share\info') {
  $path = Join-Path "$Stage\vendor\liblouis" $extra
  if (Test-Path $path) { Remove-Item $path -Recurse -Force }
}

# Offline recognition ships as RUNTIME ONLY (sherpa-onnx, in pylibs above).
# The Nemotron model itself is an optional ~650 MB user-driven download into
# %LOCALAPPDATA%\Dotify\models\ (core offline-model.js manages it) — never
# staged, so the installer stays small and upgrades never re-ship it.

# Speaker-embedding model for named speaker identification (Apache-2.0).
# Placed where speaker-id\server.py looks by default (models\ next to it),
# with the manifest download_model.py would have written.
$speakerModel = Fetch $Lock.speaker_model
$speakerModelDir = "$Stage\Speech to text\speaker-id\models"
New-Item -ItemType Directory -Force $speakerModelDir | Out-Null
Copy-Item $speakerModel "$speakerModelDir\resnet34_voxceleb_lm.onnx"
[IO.File]::WriteAllText("$speakerModelDir\model.json", (@{
  name = 'resnet34'
  file = 'resnet34_voxceleb_lm.onnx'
  dim = 256
  url = $Lock.speaker_model.url
} | ConvertTo-Json), [Text.UTF8Encoding]::new($false))

Copy-Item "$Platform\launcher.ps1" "$Stage\launcher.ps1"
Copy-Item "$Platform\docs\README-windows.md" "$Stage\README-windows.md"
Copy-Item "$Here\THIRD-PARTY-NOTICES.md" "$Stage\THIRD-PARTY-NOTICES.md"

# ---- SECRETS GATE (hard fail) ----
# *.wav / *.ref.txt / recordings\: WER-harness session recordings are raw room
# audio + speech transcripts (see recorder.js) — same class as speakers\.
$bad = @(Get-ChildItem -LiteralPath $Stage -Recurse -Force -File | Where-Object {
  $_.Name -eq '.env' -or $_.Extension -eq '.key' -or
  $_.Extension -eq '.wav' -or $_.Name -like '*.ref.txt'
})
$bad += @(Get-ChildItem -LiteralPath $Stage -Recurse -Force -Directory | Where-Object {
  $_.Name -eq 'speakers' -or $_.Name -eq 'recordings'
})
if ($bad.Count -gt 0) { throw "SECRETS GATE: refusing to package: $($bad.FullName -join '; ')" }

Write-Host "Stage OK: $Stage"

if ($Installer) {
  $iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
  ) | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $iscc) { throw 'Inno Setup 6 not found (winget install JRSoftware.InnoSetup)' }
  & $iscc (Join-Path $Here 'installer.iss')
  if ($LASTEXITCODE -ne 0) { throw 'ISCC failed' }

  $installerPath = Join-Path $Here 'Output\DotifySetup.exe'
  if ($Sign) {
    if (-not $CertificateThumbprint) {
      throw '-Sign requires -CertificateThumbprint or DOTIFY_SIGNING_CERT_THUMBPRINT'
    }
    $thumbprint = $CertificateThumbprint.Replace(' ', '').ToUpperInvariant()
    if ($thumbprint -notmatch '^[0-9A-F]{40}$') {
      throw 'Certificate thumbprint must be a 40-character SHA-1 thumbprint'
    }
    $certPath = "Cert:\$CertificateStoreLocation\My\$thumbprint"
    if (-not (Test-Path -LiteralPath $certPath)) {
      throw "Code-signing certificate not found: $certPath"
    }
    $signTool = (Get-Command signtool.exe -ErrorAction SilentlyContinue).Source
    if (-not $signTool) {
      $kits = @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin",
        "$env:ProgramFiles\Windows Kits\10\bin"
      ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
      $signTool = Get-ChildItem -LiteralPath $kits -Filter signtool.exe -Recurse `
        -ErrorAction SilentlyContinue | Sort-Object FullName -Descending |
        Select-Object -ExpandProperty FullName -First 1
    }
    if (-not $signTool) { throw 'SignTool.exe not found; install the Windows SDK' }
    $signArgs = @('sign', '/sha1', $thumbprint, '/fd', 'SHA256', '/tr', $TimestampUrl, '/td', 'SHA256')
    if ($CertificateStoreLocation -eq 'LocalMachine') { $signArgs += '/sm' }
    $signArgs += $installerPath
    & $signTool @signArgs
    if ($LASTEXITCODE -ne 0) { throw 'SignTool signing failed' }
    & $signTool verify /pa /all $installerPath
    if ($LASTEXITCODE -ne 0) { throw 'SignTool verification failed' }
    Write-Host "Signed installer: $installerPath"
  } else {
    Write-Warning 'Installer is unsigned. Use -Installer -Sign with a CA-issued certificate for distribution.'
  }
}
