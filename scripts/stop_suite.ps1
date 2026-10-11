[CmdletBinding()]
param(
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [string]$StateFile = '',
    [switch]$CancelJobs,
    [switch]$ValidateOnly
)

# A PID alone is never sufficient authority to stop a process.
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if (-not $StateFile) { $StateFile = Join-Path $projectRoot ".local\runtime\suite-$Port.json" }
$state = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
if ($state.version -ne 1 -or $state.project_root -ine $projectRoot) { throw 'Runtime state belongs to another project or format.' }
if ($state.app_url -cne "http://127.0.0.1:$Port") { throw 'Runtime state does not identify the requested loopback application port.' }
$dataPath = [IO.Path]::GetFullPath([string]$state.data_directory)
$modelPath = Join-Path $projectRoot '.local\models\qwen3.8-27b\Qwen3.8-27B-Q4_K_M.gguf'
$binaryPath = Join-Path $projectRoot '.local\llama.cpp\llama-server.exe'
function Test-Argument([string]$Command, [string]$Name, [string]$Value) {
    $literal = [regex]::Escape($Value)
    $option = [regex]::Escape($Name)
    return [regex]::IsMatch($Command, '(?:^|\s)(?:"' + $option + '"|' + $option + ')\s+(?:"' + $literal + '"|' + $literal + ')(?:\s|$)', 'IgnoreCase')
}
$processes = @()
foreach ($record in @($state.processes)) {
    if ($record.role -notin @('app', 'classifier')) { throw 'Unknown service in runtime state.' }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$record.pid)"
    if (-not $process) { continue }
    $command = [string]$process.CommandLine
    $owned = (Test-Argument $command '--host' '127.0.0.1') -and (Test-Argument $command '--port' ([string]$record.port))
    if ($record.role -eq 'classifier') {
        $owned = $owned -and $process.ExecutablePath -ieq $binaryPath -and (Test-Argument $command '--model' $modelPath) -and (Test-Argument $command '--alias' 'castwell-local')
    } else {
        $owned = $owned -and [int]$record.port -eq $Port -and $command -match '^(?:"[^"]+"|\S+)\s+(?:-u\s+)?-m\s+castwell(?:\s|$)' -and (Test-Argument $command '--data-dir' $dataPath)
    }
    if (-not $owned) { throw 'A recorded process does not belong to the project service. No services were stopped.' }
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes([string]$process.CommandLine)
        $hash = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '').ToLowerInvariant()
    } finally { $sha.Dispose() }
    # PowerShell 7 parses JSON timestamps as DateTime; Windows PowerShell keeps
    # strings. Compare UTC ticks so both retain the stored subsecond precision.
    $recordCreated = [DateTimeOffset]$record.created_utc
    if ($process.CreationDate.ToUniversalTime().Ticks -ne $recordCreated.UtcDateTime.Ticks -or $hash -cne $record.command_sha256) {
        throw 'A recorded process identity changed. No services were stopped.'
    }
    $processes += @{ record=$record; process=$process }
}
$appRecord = @($processes | Where-Object { $_.record.role -eq 'app' })
if ($appRecord.Count -gt 1) { throw 'Runtime state contains multiple applications.' }
if ($ValidateOnly) {
    [pscustomobject]@{ Validated=$true; StopsServices=$false; Services=$processes.Count }
    return
}
if ($appRecord.Count) {
    Add-Type -AssemblyName System.Net.Http
    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.UseProxy = $false
    $handler.AllowAutoRedirect = $false
    $client = [System.Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(5)
    try {
        foreach ($path in @('/api/feeds/refresh/status', '/api/models/status')) {
            $response = $client.GetAsync($state.app_url + $path).GetAwaiter().GetResult()
            try {
                [void]$response.EnsureSuccessStatusCode()
                $work = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json
                if ($work.status -eq 'running') { throw 'Subscription refresh or speech-model preparation is active. Let it finish before stopping services.' }
            } finally { $response.Dispose() }
        }
        function Get-Queue {
            $response = $client.GetAsync($state.app_url + '/api/jobs').GetAwaiter().GetResult()
            try {
                [void]$response.EnsureSuccessStatusCode()
                return @((($response.Content.ReadAsStringAsync().GetAwaiter().GetResult()) | ConvertFrom-Json).jobs)
            } finally { $response.Dispose() }
        }
        $jobs = @(Get-Queue)
        if ($jobs.Count -and -not $CancelJobs) { throw 'Processing is active. Wait for completion or explicitly use -CancelJobs to preserve completed stages and stop.' }
        if ($jobs.Count) {
            foreach ($job in $jobs) {
                $content = [System.Net.Http.StringContent]::new('{}', [Text.Encoding]::UTF8, 'application/json')
                $id = [Uri]::EscapeDataString([string]$job.episode_id)
                $response = $client.PostAsync($state.app_url + '/api/episodes/' + $id + '/cancel', $content).GetAwaiter().GetResult()
                try { [void]$response.EnsureSuccessStatusCode() } finally { $response.Dispose(); $content.Dispose() }
            }
            $timer = [Diagnostics.Stopwatch]::StartNew()
            while (@(Get-Queue).Count -and $timer.Elapsed.TotalSeconds -lt 60) { Start-Sleep -Milliseconds 500 }
            if (@(Get-Queue).Count) { throw 'A job has not reached a safe cancellation point. No services were stopped; retry after it finishes.' }
        }
    } finally { $client.Dispose(); $handler.Dispose() }
}
# All fingerprints were checked before the first stop. Verify again immediately
# before each stop to protect against PID reuse during queue cancellation.
foreach ($entry in @($processes | Sort-Object { if ($_.record.role -eq 'app') { 0 } else { 1 } })) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$entry.record.pid)"
    if (-not $process) { continue }
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $hash = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes([string]$process.CommandLine))).Replace('-', '').ToLowerInvariant() } finally { $sha.Dispose() }
    $recordCreated = [DateTimeOffset]$entry.record.created_utc
    if ($process.CreationDate.ToUniversalTime().Ticks -ne $recordCreated.UtcDateTime.Ticks -or $hash -cne $entry.record.command_sha256) { throw 'Process identity changed; refusing this stop.' }
    Stop-Process -Id $process.ProcessId
    Wait-Process -Id $process.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
}
[pscustomobject]@{ Stopped=$true; Services=$processes.Count; LibraryDeleted=$false }
