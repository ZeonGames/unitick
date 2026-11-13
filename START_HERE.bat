@echo off
setlocal
title unitick
cd /d "%~dp0"
set "LOG=%~dp0setup_log.txt"
set "REPORTS=%USERPROFILE%\unitick\reports"

echo.
echo  ===============================
echo   unitick  -  match analyzer
echo  ===============================
echo.

if not exist "%~dp0pyproject.toml" goto notextracted

rem ---- downloaded files are "blocked" by Windows; unblock them quietly
powershell -NoProfile -Command "Get-ChildItem -LiteralPath '%~dp0' -Recurse | Unblock-File" >nul 2>nul

rem ---- find python 3.11+
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>nul && set "PY=py -3"
if defined PY goto havepy
python -c "import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)" >nul 2>nul && set "PY=python"
if defined PY goto havepy
goto installpy

:havepy
echo [ok] python found
if exist "%~dp0.venv\Scripts\python.exe" goto havevenv
echo [..] creating a private python environment for unitick
%PY% -m venv "%~dp0.venv" >>"%LOG%" 2>&1
if not exist "%~dp0.venv\Scripts\python.exe" goto fail

:havevenv
set "VPY=%~dp0.venv\Scripts\python.exe"
"%VPY%" -c "import unitick, awpy, polars" >nul 2>nul
if not errorlevel 1 goto ready

echo [..] installing unitick - first run only, takes 2 to 5 minutes
echo      progress is written to setup_log.txt
"%VPY%" -m pip install --upgrade pip >>"%LOG%" 2>&1
"%VPY%" -m pip install -e ".[cs2]" >>"%LOG%" 2>&1
if errorlevel 1 goto fail
rem older CPUs without AVX2 need the compatibility build of polars
"%VPY%" -c "import polars; polars.DataFrame({'a':[1]}).sum()" >nul 2>nul
if errorlevel 1 "%VPY%" -m pip install polars-lts-cpu >>"%LOG%" 2>&1
echo [..] downloading CS2 map files - optional, skipped if offline
"%VPY%" -c "import awpy.data as d; d.update()" >>"%LOG%" 2>&1
"%VPY%" -c "import unitick, awpy, polars" >>"%LOG%" 2>&1
if errorlevel 1 goto fail

:ready
echo [ok] unitick is installed
if not exist "%REPORTS%" mkdir "%REPORTS%"

:menu
echo.
echo   1  open the unitick app in your browser: every match, drop in new replays
echo   2  watch mode: analyze every new match automatically while you play
echo   3  analyze the replays already on this PC
echo   4  analyze one file: drag a .vrf / .dem / R6 folder into this window
echo   5  sample reports - real VALORANT, CS2 and R6 matches, no game needed
echo   6  download real sample replays (about 140 MB) and analyze them
echo   7  open my reports with video playback - local server, keep window open
echo   8  open my reports folder
echo   9  quit
echo.
set "C="
set /p "C=choose 1-9 and press enter: "
if "%C%"=="1" goto app
if "%C%"=="2" goto watch
if "%C%"=="3" goto existing
if "%C%"=="4" goto onefile
if "%C%"=="5" goto sample
if "%C%"=="6" goto download
if "%C%"=="7" goto serve
if "%C%"=="8" start "" explorer "%REPORTS%" & goto menu
if "%C%"=="9" exit /b 0
goto menu

:app
if exist "%~dp0web\dist\index.html" goto apprun
where npm >nul 2>nul
if errorlevel 1 echo [!!] the app UI is not built and Node.js is not installed. get Node 18+ from nodejs.org, then try again & goto menu
echo [..] building the app UI - first run only
pushd "%~dp0web"
call npm install >>"%LOG%" 2>&1 && call npm run build >>"%LOG%" 2>&1
popd
if not exist "%~dp0web\dist\index.html" echo [!!] building the UI failed - see setup_log.txt & goto menu
:apprun
echo [ok] unitick app at http://localhost:8765 - keep this window open, close it to stop
"%VPY%" -m unitick.cli app
goto menu

:watch
echo.
echo keep this window open while you play. close it to stop.
"%VPY%" -m unitick.cli watch --open --new-only
goto menu

:existing
"%VPY%" -m unitick.cli watch --once
start "" explorer "%REPORTS%"
goto menu

:onefile
set "F="
set /p "F=drag the file here, then press enter: "
set "F=%F:"=%"
if not exist "%F%" echo file not found: %F% & goto menu
for %%I in ("%F%") do set "OUT=%REPORTS%\%%~nI.html"
"%VPY%" -m unitick.cli report "%F%" -o "%OUT%"
if errorlevel 1 echo [!!] that failed - screenshot this window and send it & goto menu
start "" "%OUT%"
goto menu

:sample
"%VPY%" -m unitick.cli samples
if errorlevel 1 echo [!!] samples failed - screenshot this window and send it & goto menu
echo [ok] opening the samples through a local server so videos can play
echo      close this window or press ctrl+c to stop it
"%VPY%" -m unitick.cli serve "%REPORTS%\samples"
goto menu

:serve
echo opening your reports through a local server so videos can play
echo close this window or press ctrl+c to stop it
"%VPY%" -m unitick.cli serve "%REPORTS%"
goto menu

:download
"%VPY%" "%~dp0samples\download_samples.py"
"%VPY%" -m unitick.cli samples --raw
"%VPY%" -m unitick.cli serve "%REPORTS%\samples"
goto menu

:installpy
echo [!!] python 3.11 or newer is not installed
echo [..] installing python 3.12 with winget...
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
echo.
echo if that worked: CLOSE this window and double-click START_HERE.bat again.
echo if winget is missing: install python from python.org and tick "Add python.exe to PATH".
pause
exit /b 1

:notextracted
echo [!!] you are running this from inside the zip.
echo      right-click unitick.zip, choose "Extract All", open the extracted
echo      unitick folder, and double-click START_HERE.bat there.
pause
exit /b 1

:fail
echo.
echo [!!] setup failed. last lines of setup_log.txt:
echo ---------------------------------------------
powershell -NoProfile -Command "Get-Content -LiteralPath '%LOG%' -Tail 25"
echo ---------------------------------------------
echo send me a screenshot of this window, or the setup_log.txt file.
pause
exit /b 1
