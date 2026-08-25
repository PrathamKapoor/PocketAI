@echo off
setlocal EnableExtensions
rem ============================================================
rem  PocketAI - benchmark.bat
rem  Full runtime benchmark:
rem    1) llama-bench  - raw throughput (pp512 / tg128) at
rem       physical-core and logical-core thread counts
rem    2) bench_server.ps1 - server startup+load time, peak RAM,
rem       CPU utilization, end-to-end API speed and stability
rem  Results: printed here and saved under logs\
rem ============================================================

for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "LLAMA_DIR=%~dp0llama.cpp"
set "MODEL=%ROOT%\models\Qwen3.5-4B-Q4_K_M.gguf"
set "LOG_DIR=%ROOT%\logs"
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "OUT=%LOG_DIR%\benchmark_llama_bench.txt"

if not exist "%LLAMA_DIR%\llama-bench.exe" (
    echo [PocketAI] ERROR: llama-bench.exe not found in %LLAMA_DIR%
    exit /b 1
)
if not exist "%MODEL%" (
    echo [PocketAI] ERROR: model not found: %MODEL%
    exit /b 1
)
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

rem --- detect cores ---
set "THREADS=4"
set "THREADS_BATCH=8"
for /f "usebackq delims=" %%n in (`""%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0detect_cores.ps1""`) do set "%%n"

echo ============================================================
echo PocketAI benchmark - %DATE% %TIME%
echo CPU threads: %THREADS% physical / %THREADS_BATCH% logical
echo ============================================================

echo.
echo [1/3] llama-bench with %THREADS% threads (physical cores)...
echo PocketAI benchmark %DATE% %TIME% - threads=%THREADS% > "%OUT%"
"%LLAMA_DIR%\llama-bench.exe" -m "%MODEL%" -t %THREADS% -b 512 -ub 512 -fa on -ngl 0 -p 512 -n 128 -r 2 >> "%OUT%" 2>&1

echo [2/3] llama-bench with %THREADS_BATCH% threads (logical cores)...
echo. >> "%OUT%"
echo PocketAI benchmark %DATE% %TIME% - threads=%THREADS_BATCH% >> "%OUT%"
"%LLAMA_DIR%\llama-bench.exe" -m "%MODEL%" -t %THREADS_BATCH% -b 512 -ub 512 -fa on -ngl 0 -p 512 -n 128 -r 2 >> "%OUT%" 2>&1

type "%OUT%"

echo.
echo [3/3] server benchmark (startup/load time, RAM, CPU, API speed)...
echo        results appended to %LOG_DIR%\benchmark_server.txt
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0bench_server.ps1"

echo.
echo [PocketAI] Benchmark done. Results:
echo   %OUT%
echo   %LOG_DIR%\benchmark_server.txt
echo   %LOG_DIR%\llama-server.log
endlocal
exit /b 0
