[CmdletBinding()]
param(
    [string]$Model = (Join-Path $PSScriptRoot '..\.local\models\qwen2.5-7b-instruct\qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf'),
    [string]$ServerBinary = (Join-Path $PSScriptRoot '..\.local\llama.cpp\llama-server.exe'),
    [ValidateRange(1, 65535)][int]$Port = 8081,
    [ValidateRange(1, 128)][int]$Threads = 8,
    [ValidateRange(2048, 131072)][int]$Context = 8192,
    [ValidateRange(-1, 999)][int]$GpuLayers = -1,
    [switch]$Background
)

# Run the installed native llama.cpp server with local weights. No downloads,
# API keys, hosted inference, Python inference packages, or Ollama are needed.
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$modelPath = (Resolve-Path -LiteralPath $Model).Path
$binaryPath = (Resolve-Path -LiteralPath $ServerBinary).Path
if (-not (Test-Path -LiteralPath $modelPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $binaryPath -PathType Leaf)) {
    throw 'Model and server binary must both be existing files.'
}

$portCheck = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
try {
    $portCheck.Start()
} catch {
    throw "Port $Port is already occupied. Stop your previous local server or choose -Port."
} finally {
    $portCheck.Stop()
}

$layers = if ($GpuLayers -eq -1) { 'all' } else { [string]$GpuLayers }
$nativeArgs = @(
    '--model', $modelPath, '--alias', 'castwell-local',
    '--host', '127.0.0.1', '--port', [string]$Port,
    '--threads', [string]$Threads, '--threads-batch', [string]$Threads,
    '--ctx-size', [string]$Context, '--n-gpu-layers', $layers, '--parallel', '1'
)

Write-Host "Castwell AI endpoint: http://127.0.0.1:$Port/v1"
Write-Host 'Castwell AI model: castwell-local'
if ($Background) {
    $logDirectory = Join-Path $projectRoot '.local\logs'
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $stdoutPath = Join-Path $logDirectory 'llama-server.stdout.log'
    $stderrPath = Join-Path $logDirectory 'llama-server.stderr.log'
    # Start-Process joins its arguments; quote whitespace in Windows paths.
    $quotedArgs = $nativeArgs | ForEach-Object {
        if ($_ -match '\s') { '"' + $_ + '"' } else { $_ }
    }
    $serverProcess = Start-Process -FilePath $binaryPath -ArgumentList $quotedArgs `
        -WorkingDirectory (Split-Path -Parent $binaryPath) -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
    $serverProcess.Id | Set-Content -LiteralPath (Join-Path $logDirectory 'llama-server.pid')
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
