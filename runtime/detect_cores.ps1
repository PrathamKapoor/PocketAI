# PocketAI - detect_cores.ps1
# Prints THREADS=<physical cores> and THREADS_BATCH=<logical processors>,
# one KEY=value per line, for consumption by the .bat launchers via for /f.
# Falls back to conservative defaults if WMI is unavailable.
$cores = $null
$logical = $null
try {
    $cores = (Get-CimInstance Win32_Processor | Measure-Object NumberOfCores -Sum).Sum
    $logical = (Get-CimInstance Win32_Processor | Measure-Object NumberOfLogicalProcessors -Sum).Sum
} catch { }
if (-not $cores -or $cores -lt 1) { $cores = 4 }
if (-not $logical -or $logical -lt $cores) { $logical = $cores }
Write-Output "THREADS=$cores"
Write-Output "THREADS_BATCH=$logical"
