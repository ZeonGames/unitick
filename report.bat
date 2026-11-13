@echo off
REM drag a CS2 .dem, a VALORANT .vrf or an R6 match folder onto this file.
REM run START_HERE.bat once first (it installs everything).
cd /d "%~dp0"
if "%~1"=="" (echo drag a .dem, .vrf or R6 match folder onto report.bat & pause & exit /b 1)
if not exist ".venv\Scripts\python.exe" (echo run START_HERE.bat first & pause & exit /b 1)
".venv\Scripts\python.exe" -m unitick.cli report "%~1" -o "%~dpn1.html"
if errorlevel 1 (pause & exit /b 1)
start "" "%~dpn1.html"
