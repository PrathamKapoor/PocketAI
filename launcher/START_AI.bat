@echo off
setlocal EnableExtensions
rem ============================================================
rem  PocketAI - START_AI.bat  (Phase 9 portable launcher)
rem  Double-click to start everything:
rem    1. Detect drive path   (all paths from %%~dp0, no drive
rem                             letter assumptions: E:/F:/G:...)
rem    2. Detect hardware     (preflight.py measures RAM)
rem    3. Select profile      (safe / normal / performance)
rem    4. Start llama-server  (runtime\start_model.bat, ctx from
rem                             the selected profile)
rem    5. Start backend       (backend\main.py on 127.0.0.1:8090)
rem    6. Open browser        (http://127.0.0.1:8090/)
rem  Stop everything with launcher\STOP_AI.bat.
rem ============================================================

for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "LOG_DIR=%ROOT%\logs"
set "PYTHON=%ROOT%\runtime\python\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ============================================================
echo  PocketAI - portable offline AI assistant
echo ============================================================
echo [PocketAI] Root: %ROOT%
echo [PocketAI] Starting PocketAI...
echo [PocketAI] Loading local AI model. This may take longer from USB 2.0.

rem --- 1. Resolve Python: bundled embeddable runtime first, ---
rem ---    then system python / py launcher as a fallback.   ---
set "PYTHON_RUNTIME=%ROOT%\runtime\python\python.exe"
if exist "%PYTHON_RUNTIME%" (
    set "PYTHON=%PYTHON_RUNTIME%"
    goto python_ok
)
set "PYTHON=python"
"%PYTHON%" -c "import sys" >nul 2>&1
if not errorlevel 1 goto python_ok
set "PYTHON=py"
"%PYTHON%" -c "import sys" >nul 2>&1
if not errorlevel 1 goto python_ok
echo [PocketAI] ERROR: No Python runtime found.
echo [PocketAI]   Expected: runtime\python\python.exe (bundled, preferred)
echo [PocketAI]   Fallback: python on PATH with backend\requirements.txt
echo [PocketAI]             installed (run launcher\install_deps.bat once).
echo [PocketAI]   Fix: restore the runtime\python folder, or install
echo [PocketAI]        Python 3.10+ from python.org.
pause
exit /b 1

:python_ok
echo [PocketAI] Python: %PYTHON%

rem --- 2. Pre-flight checks: deps, config, files, RAM, ports ---
"%PYTHON%" "%~dp0preflight.py"
if errorlevel 1 (
    echo.
    echo [PocketAI] Startup aborted. Fix the issue above and run START_AI.bat again.
    pause
    exit /b 1
)

rem --- 3. Read pre-flight results (logs\preflight.env) ---
for /f "usebackq delims=" %%n in ("%LOG_DIR%\preflight.env") do set "%%n"
if not "%OK%"=="1" (
    echo [PocketAI] ERROR: pre-flight results missing. Aborting.
    pause
    exit /b 1
)
echo [PocketAI] Profile: %PROFILE%  (server ctx=%CTX%, free RAM=%FREE_MB% MB)
rem The backend starts AFTER the model is resident, so it would measure too
rem little free RAM and downgrade. Export the launcher's decision instead.
set "POCKETAI_PROFILE=%PROFILE%"

rem --- 4. Model server on %MODEL_PORT% (profile context via POCKETAI_CTX) ---
if "%MODEL_READY%"=="1" (
    rem A server is already running. Make sure its context matches the profile
    rem we just selected: reusing a PERFORMANCE ctx on an 8 GB machine wastes
    rem RAM and risks swap. If it differs, restart it with the right size.
    call :check_ctx
)
if "%MODEL_READY%"=="1" goto model_ready
set "POCKETAI_CTX=%CTX%"
call "%ROOT%\runtime\start_model.bat"
if errorlevel 1 goto model_failed
goto model_ready
:model_failed
echo [PocketAI] ERROR: model server failed to start.
echo [PocketAI]   Log: %LOG_DIR%\llama-server.log
call :cleanup_model
pause
exit /b 1

:model_ready
rem --- 5. Backend on %BACKEND_PORT% ---
if "%BACKEND_READY%"=="1" goto backend_ready
echo [PocketAI] Starting backend on http://%BACKEND_HOST%:%BACKEND_PORT% ...
start "PocketAI-backend" /min cmd /c ""%PYTHON%" "%ROOT%\backend\main.py" >> "%LOG_DIR%\backend.log" 2>&1"

rem wait up to ~45 s for the backend to answer /health
set /a TRIES=0
:wait_backend
call :probe_backend
if not errorlevel 1 goto backend_ready
set /a TRIES+=1
if %TRIES% geq 15 goto backend_failed
"%SystemRoot%\System32\ping.exe" -n 3 127.0.0.1 >nul
goto wait_backend
:backend_failed
echo [PocketAI] ERROR: backend did not become ready within 45 s.
echo [PocketAI]   Log: %LOG_DIR%\backend.log
echo [PocketAI] Stopping the already-started model server (~4.6 GB) so the
echo [PocketAI] machine is not left starved of RAM. Run STOP_AI.bat too.
call :cleanup_model
"%PYTHON%" "%ROOT%\launcher\stop.py" --backend 2>nul
pause
exit /b 1

:backend_ready
rem --- 6. Open the UI ---
start "" "http://%BACKEND_HOST%:%BACKEND_PORT%/"
echo.
echo ============================================================
echo  PocketAI is running:  http://%BACKEND_HOST%:%BACKEND_PORT%/
echo  Profile: %PROFILE%   Model server: port %MODEL_PORT%
echo  Stop everything with: launcher\STOP_AI.bat
echo ============================================================
endlocal
exit /b 0

rem --- helpers ---------------------------------------------------------------

:probe_backend
"%PYTHON%" -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://%BACKEND_HOST%:%BACKEND_PORT%/health', timeout=2).status==200 else 1)" >nul 2>&1
exit /b %errorlevel%

:check_ctx
rem Only restart if an explicit context mismatch is detected; on any error we
rem keep the running server (fail safe, never break a working session).
if not exist "%PYTHON%" exit /b 0
"%PYTHON%" -c "import sys,json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:%MODEL_PORT%/props', timeout=2)); n=d.get('default_generation_settings',{}).get('n_ctx'); sys.exit(0 if n==int(%CTX%) else 1)" >nul 2>&1
if not errorlevel 1 exit /b 0
echo [PocketAI] Reusing a model server whose context (%CTX% expected) differs; restarting it.
call :cleanup_model
set "MODEL_READY=0"
exit /b 0

:cleanup_model
call "%ROOT%\runtime\stop_model.bat" 2>nul
taskkill /F /IM llama-server.exe >nul 2>&1
exit /b 0
