@echo off
setlocal EnableExtensions
rem ============================================================
rem  PocketAI - STOP_AI.bat  (Phase 9 portable launcher)
rem  Stops the backend and the llama.cpp model server started
rem  from THIS PocketAI folder. Processes are matched by path,
rem  so anything belonging to other apps/copies is left alone.
rem  PowerShell-free: delegates to launcher\stop.py (wmic + taskkill).
rem ============================================================

for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "PYTHON=%ROOT%\runtime\python\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

echo ============================================================
echo  PocketAI - stopping...
echo ============================================================

"%PYTHON%" "%ROOT%\launcher\stop.py"
if errorlevel 1 (
    echo [PocketAI] Some processes could not be stopped; you may need to
    echo [PocketAI] close them manually from Task Manager.
)

echo [PocketAI] Stopped. Run launcher\START_AI.bat to start again.
endlocal
exit /b 0
