# Starts Dotify: the speech server (8788), the offline speech service (8791),
# the speaker-naming service (8792) and the braille ticker with its control
# bridge (8790), then opens the page in an app-mode browser window. Quitting
# (or closing that window) ends the ticker, and the finally block stops the
# rest. -Configure prompts for API keys; -Sim runs the simulated display.
param(
  [switch]$Configure,
  [switch]$Sim,
  [switch]$NoBrowser,
  [string]$Display = 'auto',
  # 0 keeps the engine's default, sized to the display so a full page lasts
  # 5 s (JAWS's auto-advance default). Otherwise ms per cell.
  [ValidateRange(0, 60000)]
  [int]$PaceMs = 0,
  # 0 keeps the engine's default, the full display width (each refresh
  # flips a word-wrapped page). 1-40 streams that many cells per refresh.
  [ValidateRange(0, 40)]
  [int]$Window = 0
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSCommandPath
$DataDir = Join-Path $env:LOCALAPPDATA 'Dotify'
$EnvFile = Join-Path $DataDir '.env'
$Python  = Join-Path $Root 'runtime\python\python.exe'
$Node    = Join-Path $Root 'runtime\node\node.exe'
# Offline decode runs NATIVE on Windows-on-ARM when the stage ships the
# arm64 runtime: the x64 interpreter emulates there at ~4x the decode cost
# (measured RTF 0.81 emulated vs 0.19 native on a Snapdragon X Elite), which
# leaves live captions no headroom. Everything else stays on $Python — only
# the decode service is speed-critical. ARCHITEW6432 covers a launcher shell
# that is itself running emulated.
$PythonArm64 = Join-Path $Root 'runtime\python-arm64\python.exe'
$RecognizerPython = if (($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or
    $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') -and (Test-Path $PythonArm64)) {
  $PythonArm64
} else {
  $Python
}
$ProfileFile = Join-Path $Root 'Text to Braille\display_profiles.json'
$env:DOTIFY_LIBLOUIS_DIR = Join-Path $Root 'vendor\liblouis'
$env:DOTIFY_DISPLAY = $Display
$env:DOTIFY_ENV_FILE = $EnvFile
$env:DOTIFY_TRANSCRIPT_FILE = Join-Path $DataDir 'transcript.txt'
# Voiceprints are personal data: they live with the user's other Dotify data,
# never inside the installed program image.
$env:DOTIFY_SPEAKERS_DIR = Join-Path $DataDir 'speakers'
$LogDir = Join-Path $DataDir 'logs'

function Show-LauncherError([string]$Message) {
  try {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
      $Message,
      'Dotify',
      [System.Windows.MessageBoxButton]::OK,
      [System.Windows.MessageBoxImage]::Error
    ) | Out-Null
  } catch {
    Write-Error $Message
  }
}

trap {
  Show-LauncherError ("Dotify could not start.`n`n" + $_.Exception.Message +
    "`n`nDiagnostic logs: " + $LogDir)
  exit 1
}

function Wait-ForSpeechServer([string]$Url) {
  $deadline = [DateTime]::UtcNow.AddSeconds(10)
  do {
    try {
      Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 1 | Out-Null
      return
    } catch {
      Start-Sleep -Milliseconds 100
    }
  } while ([DateTime]::UtcNow -lt $deadline)
  throw "Speech server did not become ready at $Url"
}

function Wait-ForTcpPort([int]$Port, [int]$Seconds = 15) {
  $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
  do {
    $client = [Net.Sockets.TcpClient]::new()
    try {
      $result = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
      if ($result.AsyncWaitHandle.WaitOne(200) -and $client.Connected) {
        $client.EndConnect($result)
        return
      }
    } catch {
      # Startup probing is expected to fail until the model finishes loading.
    } finally {
      $client.Dispose()
    }
    Start-Sleep -Milliseconds 100
  } while ([DateTime]::UtcNow -lt $deadline)
  throw "Local service did not become ready on port $Port"
}

function Stop-OrphanedServices {
  # A launcher that died without running its finally block (killed, crashed,
  # signed out) leaves its hidden services holding Dotify's ports, and they
  # have no window to close. Stop listeners that run from this install's own
  # runtimes and whose launcher is gone. A session whose launcher is still
  # running is left alone, so a second launch fails loudly instead of
  # killing it.
  $ours = @($Python, $PythonArm64, $Node) | ForEach-Object { [IO.Path]::GetFullPath($_) }
  foreach ($port in 8788, 8790, 8791, 8792) {
    $owners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
      Select-Object -ExpandProperty OwningProcess -Unique)
    foreach ($id in $owners) {
      $process = Get-CimInstance Win32_Process -Filter "ProcessId = $id" -ErrorAction SilentlyContinue
      if (-not $process -or -not $process.ExecutablePath) { continue }
      if ($ours -notcontains [IO.Path]::GetFullPath($process.ExecutablePath)) { continue }
      if (Get-Process -Id $process.ParentProcessId -ErrorAction SilentlyContinue) { continue }
      Write-Host "Stopping a leftover Dotify service on port $port (process $id)."
      Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
    }
  }
}

function Open-SpeechBrowser([string]$Url) {
  # Dotify's normal recognizer is offline and browser-independent. Prefer the
  # Edge already present on Windows 11, then Chrome, before using the default.
  $candidates = @(
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
  ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }

  $browser = $candidates | Select-Object -First 1
  if ($browser) {
    # A dedicated app-mode window, not a tab in the user's regular browser:
    # Dotify gets its own window with no shared tabs, so Quit Dotify can close
    # the whole window (the page calls window.close(), which the browser
    # permits for a single-entry app window) without ever taking the user's
    # other tabs with it.
    Start-Process -FilePath $browser -ArgumentList @("--app=$Url")
    return
  }

  Write-Warning 'Edge/Chrome was not found. Opening the default browser.'
  # No app mode here; if the browser then refuses window.close() on Quit
  # Dotify, the page's stopped notice ("close this window and relaunch")
  # remains the fallback.
  Start-Process $Url
}

if ($Configure) {
  New-Item -ItemType Directory -Force $DataDir | Out-Null
  # Merge with the existing .env: the browser page saves provider keys the
  # prompts below don't cover (e.g. DEEPGRAM_API_KEY) into this same file -
  # a rewrite keeping only the prompted keys would silently delete them.
  $saved = [ordered]@{}
  if (Test-Path -LiteralPath $EnvFile) {
    foreach ($line in @(Get-Content -LiteralPath $EnvFile)) {
      if ($line -match '^\s*([^#=\s]+)\s*=(.*)$') { $saved[$Matches[1]] = $Matches[2] }
    }
  }
  Write-Host 'Optional transcription API keys (press Enter to keep the saved value).'
  Write-Host 'Without a key, Dotify still transcribes after the one-time Nemotron offline model download (Settings > Transcription > Offline model).'
  $aai = Read-Host 'AssemblyAI API key'
  $oai = Read-Host 'OpenAI API key'
  if ($aai) { $saved['ASSEMBLYAI_API_KEY'] = $aai }
  if ($oai) { $saved['OPENAI_API_KEY'] = $oai }
  $lines = @($saved.Keys | ForEach-Object { "$_=$($saved[$_])" })
  Set-Content -Path $EnvFile -Value $lines -Encoding ascii
  Write-Host "Saved securely outside the installed program at $EnvFile."
  return
}

if (-not $Sim) {
  $registry = Get-Content -LiteralPath $ProfileFile -Raw | ConvertFrom-Json
  # Bluetooth profiles are valid -Display picks but have no USB identity, so
  # they join validation only; the usbipd preparation below stays USB-scoped.
  $nativeProfiles = @($registry.profiles | Where-Object { $_.native_supported }) +
    @($registry.bluetooth_profiles | Where-Object { $_.native_supported })
  $usbProfiles = @($nativeProfiles | Where-Object {
    $_.transport -eq 'humanware-hid' -or $_.transport -eq 'hims-hid'
  })
  if ($Display -ne 'auto') {
    $selectedProfile = $nativeProfiles | Where-Object { $_.id -eq $Display } | Select-Object -First 1
    if (-not $selectedProfile) {
      $choices = ($nativeProfiles.id -join ', ')
      throw "Unknown or unsupported display profile '$Display'. Choose: auto, $choices"
    }
  }

  # usbipd's Shared filter leaves descriptors visible but makes live HID I/O
  # fail with error 1167. Remove that binding before starting native HID.
  $usbipd = Get-Command usbipd -ErrorAction SilentlyContinue
  if ($usbipd) {
    try {
      $state = (& $usbipd.Source state | Out-String) | ConvertFrom-Json
      $found = @()
      foreach ($device in $state.Devices) {
        foreach ($profile in $usbProfiles) {
          if ($Display -ne 'auto' -and $profile.id -ne $Display) { continue }
          $usbId = "VID_$($profile.vid)&PID_$($profile.pid)"
          if ($device.InstanceId -like "*$usbId*") {
            $found += [pscustomobject]@{ Device = $device; Profile = $profile }
          }
        }
      }
      if ($Display -eq 'auto' -and $found.Count -gt 1) {
        $names = ($found.Profile.name -join ', ')
        throw "Multiple supported braille displays are connected ($names). Start with -Display <profile-id>."
      }
      $match = $found | Select-Object -First 1
      if ($match -and $match.Device.StubInstanceId) {
        if ($match.Device.ClientIPAddress) {
          & $usbipd.Source detach --busid $match.Device.BusId
          if ($LASTEXITCODE -ne 0) { throw 'usbipd detach failed' }
        }
        Write-Host "$($match.Profile.name) is shared with WSL. Approve the UAC prompt to switch it to native Windows HID."
        $unbind = Start-Process -FilePath $usbipd.Source `
          -ArgumentList @('unbind', '--busid', $match.Device.BusId) `
          -Verb RunAs -Wait -PassThru
        if ($unbind.ExitCode -ne 0) {
          throw "usbipd unbind failed ($($unbind.ExitCode))"
        }
      }
    } catch {
      # Rethrow into the MessageBox trap: the installed shortcuts run this
      # script -WindowStyle Hidden, so a Write-Host + exit here is invisible
      # (double-click, nothing happens - e.g. a cancelled UAC elevation).
      throw "Could not prepare the braille display for native HID: $_"
    }
  }
}

New-Item -ItemType Directory -Force $DataDir | Out-Null
try {
  Stop-OrphanedServices
} catch {
  Write-Warning "Could not check for leftover Dotify services: $_"
}
$server = Start-Process -FilePath $Node `
  -ArgumentList "`"$Root\Speech to text\server.js`"" -PassThru -WindowStyle Hidden
$speechUrl = 'http://127.0.0.1:8788'
$ticker = $null
$recognizer = $null
$speakerId = $null
try {
  # Inside the try, so a readiness timeout still stops node.exe.
  Wait-ForSpeechServer $speechUrl
  if ($server.HasExited) {
    # The port answered but our server is dead: another session holds 8788.
    throw ('The speech server exited immediately after starting - a leftover ' +
      'Dotify session probably still holds port 8788. Close any running ' +
      'Dotify windows, wait a few seconds, and double-click Launch Dotify again.')
  }
  Write-Host "Speech server: $speechUrl"
  if ($Sim) {
    if (-not $NoBrowser) { Open-SpeechBrowser $speechUrl }
    # --advance-mode auto is explicit because the sim has no reader to flip
    # pages; in manual mode verify.ps1's sim step would hang.
    $simPace = if ($PaceMs -ge 1) { @('--pace', $PaceMs) } else { @() }
    & $Python "$Root\Text to Braille\run.py" --file "$Root\Text to Braille\sample.txt" --sink sim @simPace --advance-mode auto
  } else {
    New-Item -ItemType Directory -Force $LogDir | Out-Null
    $recognizerStdout = Join-Path $LogDir 'speech-output.log'
    $recognizerStderr = Join-Path $LogDir 'speech-error.log'
    # Offline decode service. The model is an optional ~650 MB download into
    # the user's data folder; the service starts without it and picks it up
    # once downloaded.
    $modelPath = Join-Path $DataDir 'models\nemotron-3.5-560ms-int8'
    $manifestPath = Join-Path $Root 'Speech to text\offline-model.json'
    $recognizerArguments = '"{0}" --model "{1}" --manifest "{2}" --port 8791' -f `
      (Join-Path $Root 'Text to Braille\local_speech_server.py'), $modelPath, $manifestPath
    $recognizer = Start-Process -FilePath $RecognizerPython -ArgumentList $recognizerArguments `
      -PassThru -WindowStyle Hidden `
      -RedirectStandardOutput $recognizerStdout -RedirectStandardError $recognizerStderr
    try {
      Wait-ForTcpPort 8791 5
    } catch {
      # Keyed providers remain usable if the offline service cannot start.
      # Preserve its diagnostic log.
      Write-Warning "The offline speech service did not start; models with API keys are unaffected."
    }

    # Speaker naming (local voiceprints). Optional: without it speakers are
    # labelled A:/B:.
    $speakerIdStdout = Join-Path $LogDir 'speaker-id-output.log'
    $speakerIdStderr = Join-Path $LogDir 'speaker-id-error.log'
    $speakerIdArguments = '"{0}"' -f (Join-Path $Root 'Speech to text\speaker-id\server.py')
    $speakerId = Start-Process -FilePath $Python -ArgumentList $speakerIdArguments `
      -PassThru -WindowStyle Hidden `
      -RedirectStandardOutput $speakerIdStdout -RedirectStandardError $speakerIdStderr
    try {
      Wait-ForTcpPort 8792 15
    } catch {
      Write-Warning "Speaker naming service did not start; A:/B: labels still work."
    }

    $tickerStdout = Join-Path $LogDir 'ticker-output.log'
    $tickerStderr = Join-Path $LogDir 'ticker-error.log'
    # --pace and --window only when asked for; run.py's defaults fit the
    # connected display.
    $tickerArguments = '"{0}" --source ws --sink auto-display --display {1}' -f `
      (Join-Path $Root 'Text to Braille\windows_run.py'), $Display
    if ($PaceMs -ge 1) {
      $tickerArguments = '{0} --pace {1}' -f $tickerArguments, $PaceMs
    }
    if ($Window -ge 1) {
      $tickerArguments = '{0} --window {1}' -f $tickerArguments, $Window
    }
    $ticker = Start-Process -FilePath $Python -ArgumentList $tickerArguments `
      -PassThru -WindowStyle Hidden `
      -RedirectStandardOutput $tickerStdout -RedirectStandardError $tickerStderr

    try {
      Wait-ForSpeechServer 'http://127.0.0.1:8790/health'
    } catch {
      if ($ticker.HasExited) {
        $detail = if (Test-Path $tickerStderr) { Get-Content $tickerStderr -Raw } else { '' }
        throw "Braille display connection failed. $detail"
      }
      throw
    }
    if ($ticker.HasExited) {
      # The probe answered but our ticker is dead: another session's bridge
      # holds 8790. Don't open a browser wired to it.
      $detail = if (Test-Path $tickerStderr) { Get-Content $tickerStderr -Raw } else { '' }
      throw ('The braille ticker exited right after starting - a leftover ' +
        'Dotify session probably still holds port 8790. Close any running ' +
        "Dotify windows and relaunch. $detail")
    }

    if (-not $NoBrowser) { Open-SpeechBrowser $speechUrl }
    Wait-Process -Id $ticker.Id
  }
} finally {
  if ($ticker -and -not $ticker.HasExited) { Stop-Process -Id $ticker.Id -Force }
  if ($recognizer -and -not $recognizer.HasExited) { Stop-Process -Id $recognizer.Id -Force }
  if ($speakerId -and -not $speakerId.HasExited) { Stop-Process -Id $speakerId.Id -Force }
  if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force }
}
