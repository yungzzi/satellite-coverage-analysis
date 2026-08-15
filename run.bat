@echo off
cd /d %~dp0
set "VENV=.venv"
if not exist "%VENV%\Scripts\python.exe" if exist "..\Œ¿–«∏≤∏«–‘∑÷Œˆ\.venv\Scripts\python.exe" set "VENV=..\Œ¿–«∏≤∏«–‘∑÷Œˆ\.venv"
if not exist "%VENV%\Scripts\python.exe" (
  echo [ERROR] Python env not found: .venv
  pause
  exit /b 1
)
if exist config.toml (
  "%VENV%\Scripts\python" src\coverage_analysis.py --config config.toml %*
) else (
  "%VENV%\Scripts\python" src\coverage_analysis.py %*
)
