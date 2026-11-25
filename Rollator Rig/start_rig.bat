@echo off
rem Rollator rig: starts the website and the watcher, then opens the sandbox page.
rem Double-click this file. Close the two windows it opens to stop everything.
rem
rem If the Mega's COM number changes, edit MEGA_PORT (run "python rig_driver.py ports" in the
rem pipeline folder to list ports). ESP_PORT=auto picks the one other USB serial port; set it to
rem e.g. COM7 if that ever guesses wrong, or to nothing to run without the sensor kit.
set MEGA_PORT=COM6
set ESP_PORT=auto

set ROOT=%~dp0
set PYEXE=python
if exist "%ROOT%pipeline\.venv\Scripts\python.exe" set PYEXE=.venv\Scripts\python.exe
set ESP_ARG=
if not "%ESP_PORT%"=="" set ESP_ARG=--esp-port %ESP_PORT%

echo Starting the website...
start "Rollator website" /D "%ROOT%web" cmd /k "npm install --no-audit --no-fund && npm run dev"
echo Starting the watcher (rig on %MEGA_PORT%, sensor kit: %ESP_PORT%)...
start "Rollator watcher" /D "%ROOT%pipeline" cmd /k %PYEXE% rig_driver.py watch --port %MEGA_PORT% %ESP_ARG%
echo Opening the sandbox page in a few seconds (refresh it if it isn't ready yet)...
timeout /t 12 /nobreak >nul
start "" http://localhost:3000/sim
