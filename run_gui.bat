@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo   Satellite coverage analysis workbench
echo   Open http://localhost:8501 if the browser does not open.
echo   Close this window or press Ctrl+C to stop the service.
echo ============================================================

rem Prefer this worktree's venv. Otherwise reuse the first sibling venv found.
set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  set "SHARED_PYTHON="
  for /d %%D in ("%~dp0..\*") do (
    if not defined SHARED_PYTHON if exist "%%~fD\.venv\Scripts\python.exe" (
      set "SHARED_PYTHON=%%~fD\.venv\Scripts\python.exe"
    )
  )
  if defined SHARED_PYTHON set "PYTHON=!SHARED_PYTHON!"
)

if not exist "%PYTHON%" (
  echo [ERROR] No usable Python virtual environment was found.
  echo Run these commands in this worktree:
  echo   python -m venv .venv
  echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)

"%PYTHON%" -c "import streamlit" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Streamlit is not installed in: %PYTHON%
  echo Install this project's dependencies and try again.
  pause
  exit /b 1
)

"%PYTHON%" -m streamlit run src\loader_app.py
