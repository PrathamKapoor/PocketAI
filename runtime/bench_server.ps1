<#
PocketAI - bench_server.ps1
Starts the bundled llama.cpp server, waits for readiness, then measures:
  - startup + model load time (process start -> /health 200, includes warmup)
  - peak RAM (working set) across load + generation
  - generation speed (tok/s) via /v1/chat/completions
  - prompt processing speed (tok/s) via a long-prompt request
  - CPU utilization during generation
  - stability across repeated requests

All paths are resolved relative to the repo root (drive-letter agnostic).
Requires only Windows PowerShell 5.1+ (no modules, no internet).

Usage:
  powershell -NoProfile -ExecutionPolicy Bypass -File bench_server.ps1 [-KeepRunning]
#>
param(
    [int]$Port = 8091,
    [int]$Ctx = 4096,
    [int]$Batch = 512,
    [int]$Threads = 0,      # 0 = auto-detect physical cores
    [int]$MaxTokens = 128,
    [int]$Runs = 3,
    [string]$ApiKey = 'b54260a632b845bb9cdf175def0adb0c',  # mirrors config/model.json
    [switch]$KeepRunning
)

$ErrorActionPreference = 'Stop'
$Root    = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Runtime = Join-Path $Root 'runtime\llama.cpp'
$Server  = Join-Path $Runtime 'llama-server.exe'
$Model   = Join-Path $Root 'models\Qwen3.5-4B-Q4_K_M.gguf'
$LogDir  = Join-Path $Root 'logs'
$Base    = "http://127.0.0.1:$Port"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if (-not (Test-Path $Server)) { Write-Error "llama-server.exe not found: $Server" }
if (-not (Test-Path $Model))  { Write-Error "model not found: $Model" }

if ($Threads -le 0) {
    $Threads = (Get-CimInstance Win32_Processor | Measure-Object NumberOfCores -Sum).Sum
}
$LogicalCores = (Get-CimInstance Win32_Processor | Measure-Object NumberOfLogicalProcessors -Sum).Sum

# --- stop any previous instance launched from this repo ---
Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($Runtime, [StringComparison]::OrdinalIgnoreCase) } |
    ForEach-Object {
        Write-Host "[bench] stopping previous instance (PID $($_.ProcessId))"
        Stop-Process -Id $_.ProcessId -Force
    }
Start-Sleep -Seconds 1

$ServerLog = Join-Path $LogDir 'llama-server.log'
$cmd = "-m `"$Model`" --host 127.0.0.1 --port $Port -c $Ctx -t $Threads -tb $LogicalCores -b $Batch -ub $Batch -ngl 0 -fa on -ctk f16 -ctv f16 -np 1 -a qwen3.5-4b --jinja --api-key $ApiKey --log-file `"$ServerLog`""
Write-Host "[bench] launching llama-server (threads=$Threads/$LogicalCores ctx=$Ctx batch=$Batch port=$Port)"

$auth = @{ 'Authorization' = "Bearer $ApiKey" }

$sw = [Diagnostics.Stopwatch]::StartNew()
$proc = Start-Process -FilePath $Server -ArgumentList $cmd -PassThru -WindowStyle Hidden
$peakRam = 0

# --- wait for readiness: /health returns 200 once the model is fully loaded ---
$ready = $false
while ($sw.Elapsed.TotalSeconds -lt 300) {
    if ($proc.HasExited) {
        Write-Error "llama-server exited during startup (code $($proc.ExitCode)). Check $ServerLog"
    }
    try {
        $r = Invoke-WebRequest -Uri "$Base/health" -UseBasicParsing -TimeoutSec 2 -Headers $auth
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
    try {
        $ws = (Get-Process -Id $proc.Id).WorkingSet64
        if ($ws -gt $peakRam) { $peakRam = $ws }
    } catch { }
    Start-Sleep -Milliseconds 500
}
if (-not $ready) {
    Stop-Process -Id $proc.Id -Force
    Write-Error "server did not become ready within 300 s"
}
$loadTime = [math]::Round($sw.Elapsed.TotalSeconds, 2)

function Get-RamMB([System.Diagnostics.Process]$p) {
    return [math]::Round($p.WorkingSet64 / 1MB, 0)
}

Write-Host ""
Write-Host "=== PocketAI server benchmark ==="
Write-Host "model           : $([System.IO.Path]::GetFileName($Model))"
Write-Host "threads         : $Threads physical ($LogicalCores logical)"
Write-Host "startup+load    : $loadTime s (includes warmup)"
Write-Host "RAM after load  : $(Get-RamMB (Get-Process -Id $proc.Id)) MB"

# --- generation benchmark (repeated runs = stability check) ---
$sysMsg  = @{ role = 'system'; content = 'You are a helpful assistant. Answer concisely.' }
$userMsg = @{ role = 'user'; content = 'Explain in two or three sentences why the sky is blue.' }
$body = @{
    model = 'qwen3.5-4b'
    messages = @($sysMsg, $userMsg)
    max_tokens = $MaxTokens
    temperature = 0.0
    stream = $false
} | ConvertTo-Json -Depth 5

$tgSpeeds = @()
$failures = 0
for ($i = 1; $i -le $Runs; $i++) {
    $p = Get-Process -Id $proc.Id
    $cpuStart = $p.TotalProcessorTime.TotalMilliseconds
    $t = [Diagnostics.Stopwatch]::StartNew()
    try {
        $resp = Invoke-RestMethod -Uri "$Base/v1/chat/completions" -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 600 -Headers $auth
        $t.Stop()
        $tok = $resp.usage.completion_tokens
        $cpuEnd = (Get-Process -Id $proc.Id).TotalProcessorTime.TotalMilliseconds
        $cpuPct = [math]::Round(($cpuEnd - $cpuStart) / ($t.ElapsedMilliseconds * $LogicalCores) * 100, 0)
        $tg = [math]::Round($tok / $t.Elapsed.TotalSeconds, 2)
        $tgSpeeds += $tg
        $ws = (Get-Process -Id $proc.Id).WorkingSet64
        if ($ws -gt $peakRam) { $peakRam = $ws }
        Write-Host ("gen run {0}/{1}: {2} tokens in {3}s = {4} tok/s | CPU {5}% | RAM {6} MB" -f $i, $Runs, $tok, [math]::Round($t.Elapsed.TotalSeconds,2), $tg, $cpuPct, (Get-RamMB (Get-Process -Id $proc.Id)))
    } catch {
        $t.Stop()
        $failures++
        Write-Host "gen run ${i}/${Runs}: FAILED - $($_.Exception.Message)"
    }
}

# --- prompt processing benchmark (long prompt, tiny answer) ---
$filler = ('The quick brown fox jumps over the lazy dog. ' * 60)
$ppBody = @{
    model = 'qwen3.5-4b'
    messages = @($sysMsg, @{ role = 'user'; content = "Summarize in one short sentence: $filler" })
    max_tokens = 32
    temperature = 0.0
    stream = $false
} | ConvertTo-Json -Depth 5

$ppSpeed = $null
$ppTokens = 0
try {
    $t = [Diagnostics.Stopwatch]::StartNew()
    $resp = Invoke-RestMethod -Uri "$Base/v1/chat/completions" -Method Post -Body $ppBody -ContentType 'application/json' -TimeoutSec 600 -Headers $auth
    $t.Stop()
    $ppTokens = $resp.usage.prompt_tokens
    if ($resp.timings -and $resp.timings.prompt_ms -gt 0) {
        $ppSpeed = [math]::Round($resp.timings.prompt_n / ($resp.timings.prompt_ms / 1000), 2)
    } else {
        $ppSpeed = [math]::Round($ppTokens / $t.Elapsed.TotalSeconds, 2)
    }
    $ws = (Get-Process -Id $proc.Id).WorkingSet64
    if ($ws -gt $peakRam) { $peakRam = $ws }
    Write-Host "prompt proc     : $ppTokens tokens at $ppSpeed tok/s"
} catch {
    Write-Host "prompt proc     : FAILED - $($_.Exception.Message)"
}

$avgTg = $null
if ($tgSpeeds.Count -gt 0) {
    $avgTg = [math]::Round(($tgSpeeds | Measure-Object -Average).Average, 2)
}

Write-Host ""
Write-Host "=== summary ==="
Write-Host "startup+load time : $loadTime s"
Write-Host "generation speed  : $avgTg tok/s avg over $Runs runs (max_tokens=$MaxTokens)"
Write-Host "prompt processing : $ppSpeed tok/s ($ppTokens tokens)"
Write-Host "peak RAM          : $([math]::Round($peakRam / 1MB, 0)) MB"
Write-Host "failed requests   : $failures / $($Runs + 1)"
Write-Host "server log        : $ServerLog"

$report = @(
    "timestamp=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
    "startup_load_s=$loadTime",
    "tg_tok_s_avg=$avgTg",
    "pp_tok_s=$ppSpeed",
    "pp_tokens=$ppTokens",
    "peak_ram_mb=$([math]::Round($peakRam / 1MB, 0))",
    "threads=$Threads",
    "ctx=$Ctx",
    "failures=$failures"
) -join ' '
Add-Content -Path (Join-Path $LogDir 'benchmark_server.txt') -Value $report

if ($KeepRunning) {
    Write-Host "server left running at $Base (PID $($proc.Id))"
} else {
    Stop-Process -Id $proc.Id -Force
    Write-Host "server stopped"
}
