@echo off
setlocal EnableExtensions
rem ============================================================
rem  PocketAI - start_model.bat
rem  Starts the bundled llama.cpp model server on 127.0.0.1:8091.
rem
rem  Portable: every path is derived from %%~dp0, so the USB
rem  drive letter can change (E:, F:, G:, ...) freely.
rem  Settings mirror config\model.json (single source of truth).
rem ============================================================

for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "LLAMA_DIR=%~dp0llama.cpp"
set "MODEL=%ROOT%\models\Qwen3.5-4B-Q4_K_M.gguf"
set "LOG_DIR=%ROOT%\logs"
rem Full path to PowerShell: kept only as a fallback below.
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
rem Bundled Python drives the health probe so we never depend on PowerShell
rem being available or unblocked on locked-down school PCs.
set "PYTHON=%ROOT%\runtime\python\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

rem --- server settings (mirror config\model.json) ---
set "HOST=127.0.0.1"
set "PORT=8091"
set "CTX=4096"
rem Launcher override: START_AI.bat sets POCKETAI_CTX to the selected
rem profile's recommended_server_context (config\profiles\*.json).
if defined POCKETAI_CTX set "CTX=%POCKETAI_CTX%"
set "BATCH=512"
set "UBATCH=512"
set "ALIAS=qwen3.5-4b"
set "API_KEY=b54260a632b845bb9cdf175def0adb0c"

if not exist "%LLAMA_DIR%\llama-server.exe" (
    echo [PocketAI] ERROR: llama-server.exe not found in %LLAMA_DIR%
    exit /b 1
)
if not exist "%MODEL%" (
    echo [PocketAI] ERROR: model not found: %MODEL%
    exit /b 1
)
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

rem --- detect cores: physical for generation, logical (SMT) for batches ---
set "THREADS=4"
set "THREADS_BATCH=8"
if exist "%PYTHON%" (
    for /f "usebackq delims=" %%n in (`"%PYTHON%" "%~dp0detect_cores.py"`) do set "%%n"
) else (
    for /f "usebackq delims=" %%n in (`"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0detect_cores.ps1"`) do set "%%n"
)

rem --- already running? ---
call :probe
if not errorlevel 1 (
    echo [PocketAI] Model server already running at http://%HOST%:%PORT%
    endlocal & exit /b 0
)

echo [PocketAI] Starting model server: http://%HOST%:%PORT%  (threads=%THREADS%/%THREADS_BATCH%, ctx=%CTX%)
start "PocketAI-llama-server" /min "%LLAMA_DIR%\llama-server.exe" ^
    -m "%MODEL%" ^
    --host %HOST% --port %PORT% ^
    -c %CTX% -t %THREADS% -tb %THREADS_BATCH% -b %BATCH% -ub %UBATCH% ^
    -ngl 0 -fa on -ctk f16 -ctv f16 -np 1 ^
    -a %ALIAS% --jinja --api-key %API_KEY% ^
    --log-file "%LOG_DIR%\llama-server.log"

rem --- wait for the model to load (generous cap for slow USB sticks) ---
set /a TRIES=0
:wait_loop
call :probe
if not errorlevel 1 goto ready
set /a TRIES+=1
if %TRIES% geq 180 (
    echo [PocketAI] ERROR: server did not become ready within ~360 s.
    echo [PocketAI] Check %LOG_DIR%\llama-server.log
    echo [PocketAI] Stopping the orphaned server so it does not hold ~4.6 GB RAM.
    taskkill /F /IM llama-server.exe >nul 2>&1
    endlocal & exit /b 1
)
rem ~2 s wait (ping loopback works even without a console, unlike timeout)
"%SystemRoot%\System32\ping.exe" -n 3 127.0.0.1 >nul
goto wait_loop

:ready
echo [PocketAI] Model server ready: http://%HOST%:%PORT%  (model alias: %ALIAS%)
endlocal & exit /b 0

rem --- health probe subroutine (loopback only, uses the local API key) ---
rem Prefers the bundled Python; falls back to PowerShell only if Python is
rem missing. Returns errorlevel 0 when /health answers 200, else 1.
:probe
if exist "%PYTHON%" (
    "%PYTHON%" -c "import sys,urllib.request; r=urllib.request.Request('http://%HOST%:%PORT%/health', headers={'Authorization':'Bearer %API_KEY%'}); sys.exit(0 if urllib.request.urlopen(r, timeout=2).status==200 else 1)" >nul 2>&1
) else (
    "%PS%" -NoProfile -Command "try { $r = Invoke-WebRequest -Uri 'http://%HOST%:%PORT%/health' -UseBasicParsing -TimeoutSec 2 -Headers @{ Authorization = 'Bearer %API_KEY%' }; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
)
exit /b %errorlevel%
