@echo off
setlocal EnableExtensions
rem ============================================================
rem  PocketAI - stop_model.bat
rem  Stops the llama.cpp model server started from THIS folder.
rem  Instances are matched by executable path, so any other
rem  llama.cpp server on the machine is left alone. PowerShell-free:
rem  delegates to launcher\stop.py (wmic + taskkill).
rem ============================================================

for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "PYTHON=%ROOT%\runtime\python\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

"%PYTHON%" "%ROOT%\launcher\stop.py" --model
endlocal
exit /b 0
