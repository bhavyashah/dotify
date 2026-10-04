@echo off
rem Double-click to start Dotify on Windows.
rem The first run builds the app image (installer\stage) - needs internet once.
set "HERE=%~dp0"
if not exist "%HERE%installer\stage\launcher.ps1" (
  echo First run: building the Dotify app image; this needs internet once...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%installer\build.ps1" || goto :fail
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%installer\stage\launcher.ps1"
exit /b
:fail
echo Build failed. See the messages above.
pause
