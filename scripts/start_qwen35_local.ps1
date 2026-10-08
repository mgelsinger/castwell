[CmdletBinding()]
param(
    [string]$Model = (Join-Path $PSScriptRoot '..\.local\models\qwen3.5-35b-a3b\Qwen3.5-35B-A3B-Q4_K_M-local.gguf'),
    [string]$ServerBinary = (Join-Path $PSScriptRoot '..\.local\llama.cpp\llama-server.exe'),
    [ValidateRange(1, 65535)][int]$Port = 8081,
    [ValidateRange(1, 128)][int]$Threads = 8,
    [ValidateRange(4096, 32768)][int]$Context = 8192,
    [ValidatePattern('^(auto|all|[0-9]{1,3})$')][string]$GpuLayers = 'all',
    [ValidateRange(0, 40)][int]$CpuMoeLayers = 4,
    [ValidateRange(1, 16384)][int]$MaxTokens = 4096,
    [ValidateRange(0, 8192)][int]$ReasoningBudget = 1536,
    [ValidateRange(0, 5)][int]$LogVerbosity = 3,
    [switch]$NoThinking,
    [switch]$Background
)

# Candidate profile kept separate from the frozen Qwen3-14B launcher.
# Local files only, no API keys or downloads. Stop other model servers first.
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$modelPath = (Resolve-Path -LiteralPath $Model).Path
$binaryPath = (Resolve-Path -LiteralPath $ServerBinary).Path
if (-not (Test-Path -LiteralPath $modelPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $binaryPath -PathType Leaf)) {
    throw 'Model and server binary must be existing files.'
}
$effectiveReasoningBudget = if ($NoThinking) { 0 } else { $ReasoningBudget }
if ($effectiveReasoningBudget -ge $MaxTokens -or $MaxTokens -ge $Context) {
    throw 'The reasoning budget must be below max tokens, which must be below context.'
}
$portCheck = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
try {
    $portCheck.Start()
} catch {
    throw "Port $Port is occupied. Stop your previous local server or choose -Port."
} finally {
    $portCheck.Stop()
}
$thinkingMode = if ($NoThinking) { 'off' } else { 'on' }
$thinkingTokens = [string]$effectiveReasoningBudget
$temperature = if ($NoThinking) { '0.7' } else { '1.0' }
$topP = if ($NoThinking) { '0.8' } else { '0.95' }
$nativeArgs = @(
    '--model', $modelPath, '--alias', 'castwell-local',
    '--offline',
    '--host', '127.0.0.1', '--port', [string]$Port,
    '--threads', [string]$Threads, '--threads-batch', [string]$Threads,
    '--ctx-size', [string]$Context, '--parallel', '1',
    '--n-gpu-layers', $GpuLayers, '--fit', 'on', '--fit-target', '1536',
    '--batch-size', '512', '--ubatch-size', '128', '--flash-attn', 'on',
    '--jinja', '--reasoning', $thinkingMode, '--reasoning-format', 'deepseek',
    '--reasoning-budget', $thinkingTokens, '--n-predict', [string]$MaxTokens,
    '--temp', $temperature, '--top-p', $topP, '--top-k', '20',
    '--min-p', '0', '--presence-penalty', '1.5', '--repeat-penalty', '1.0'
    '--log-verbosity', [string]$LogVerbosity
)
if ($CpuMoeLayers -gt 0) {
    $nativeArgs += @('--n-cpu-moe', [string]$CpuMoeLayers)
}

Write-Host "Castwell AI endpoint: http://127.0.0.1:$Port/v1"
Write-Host 'Model alias: castwell-local; local Qwen3.5-35B-A3B Q4_K_M conversion'
if ($Background) {
    $logDirectory = Join-Path $projectRoot '.local\logs'
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $logStem = "qwen35-$Port"
    $stdoutPath = Join-Path $logDirectory "$logStem.stdout.log"
    $stderrPath = Join-Path $logDirectory "$logStem.stderr.log"
    $quotedArgs = $nativeArgs | ForEach-Object {
        if ($_ -match '\s') { '"' + $_ + '"' } else { $_ }
    }
    $serverProcess = Start-Process -FilePath $binaryPath -ArgumentList $quotedArgs `
        -WorkingDirectory (Split-Path -Parent $binaryPath) -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
    $serverProcess.Id | Set-Content -LiteralPath (Join-Path $logDirectory "$logStem.pid")
    [pscustomobject]@{
        ProcessId = $serverProcess.Id
        Endpoint = "http://127.0.0.1:$Port/v1"
        Model = 'castwell-local'
        StandardOutput = $stdoutPath
        StandardError = $stderrPath
    }
} else {
    & $binaryPath @nativeArgs
    exit $LASTEXITCODE
}
