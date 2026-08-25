@echo off
setlocal EnableExtensions
rem ============================================================
rem  PocketAI - install_deps.bat
rem  Installs backend\requirements.txt into a usable Python.
rem  Only needed when the bundled runtime\python is missing and
rem  a system Python is used instead. Requires internet ONCE;
rem  normal USB deployment ships runtime\python pre-built and
rem  never needs this.
rem ============================================================

for %%I in ("%~dp0..") do set "ROOT=%%~fI"

set "PYTHON=%ROOT%\runtime\python\python.exe"
if exist "%PYTHON%" goto do_install
set "PYTHON=python"
"%PYTHON%" -c "import sys" >nul 2>&1
if not errorlevel 1 goto do_install
set "PYTHON=py"
"%PYTHON%" -c "import sys" >nul 2>&1
if not errorlevel 1 goto do_install
echo [PocketAI] ERROR: No Python found. Install Python 3.10+ from python.org
echo [PocketAI]        (or restore the bundled runtime\python folder), then
echo [PocketAI]        run this script again.
pause
exit /b 1

:do_install
echo [PocketAI] Installing backend dependencies with: %PYTHON%
"%PYTHON%" -m pip install --disable-pip-version-check -r "%ROOT%\backend\requirements.txt"
if errorlevel 1 (
    echo [PocketAI] ERROR: pip install failed. Check your internet connection.
    pause
    exit /b 1
)
echo [PocketAI] Dependencies installed. You can now run launcher\START_AI.bat.
endlocal
exit /b 0
