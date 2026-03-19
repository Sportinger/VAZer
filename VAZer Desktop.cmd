@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=src"
where pythonw >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  start "" /b pythonw -m vazer desktop
  exit /b 0
)
python -m vazer desktop
