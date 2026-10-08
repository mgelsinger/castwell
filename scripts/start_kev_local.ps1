[CmdletBinding()]
param(
    [string]$Directory = (Join-Path $PSScriptRoot '..\.local'),
    [ValidateRange(1, 65535)][int]$Port = 8083,
    [switch]$Background
)

# Requires scripts/setup_kev_local.py. Uses only pinned, already cached weights.
# Kev and Qwen3 should run separately on a 24 GB GPU.
$ErrorActionPreference = 'Stop'
$localRoot = (Resolve-Path -LiteralPath $Directory).Path
$pythonPath = Join-Path $localRoot 'kev-venv\Scripts\python.exe'
$provenance = Get-Content -LiteralPath (Join-Path $localRoot 'kev-model-provenance.json') -Raw | ConvertFrom-Json
$checkpointPath = $provenance[0].path
if ($provenance[0].revision -ne '6cfce5c2fa4b4bd64026336ab649c5ca78857d52' -or
    -not (Test-Path -LiteralPath $pythonPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath (Join-Path $checkpointPath 'head.pt') -PathType Leaf)) {
    throw 'Pinned Kev runtime is missing. Run scripts/setup_kev_local.py first.'
}
$portCheck = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
try { $portCheck.Start() } finally { $portCheck.Stop() }
$environment = @{
    HF_HOME = (Join-Path $localRoot 'models\huggingface')
    HF_HUB_OFFLINE = '1'
    TRANSFORMERS_OFFLINE = '1'
    KEV_BACKEND = 'torch'
    KEV_DTYPE = 'bf16'
    KEV_ATTN = 'sdpa'
    KEV_FUSED = '0'
    KEV_CUDA_GRAPHS = '0'
    KEV_PREFIX_CACHE = '1'
    KEV_PREFIX_MAX_TOKENS = '8192'
}
$priorEnvironment = @{}
try {
    foreach ($name in $environment.Keys) {
        $priorEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $environment[$name], 'Process')
    }
    $nativeArgs = @('-u', '-m', 'kev.serve', '--run', $checkpointPath, '--host', '127.0.0.1', '--port', [string]$Port)
    Write-Host "Local Kev endpoint: http://127.0.0.1:$Port/v1/systemone; model: kev-latest"
    if ($Background) {
        $logDirectory = Join-Path $localRoot 'logs'
        New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
        $quotedArgs = $nativeArgs | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }
        $server = Start-Process -FilePath $pythonPath -ArgumentList $quotedArgs -WindowStyle Hidden `
            -WorkingDirectory $localRoot -RedirectStandardOutput (Join-Path $logDirectory "kev-$Port.stdout.log") `
            -RedirectStandardError (Join-Path $logDirectory "kev-$Port.stderr.log") -PassThru
        $server.Id | Set-Content -LiteralPath (Join-Path $logDirectory "kev-$Port.pid")
        # Windows venv Python may spawn a child. The listener PID is authoritative.
        [pscustomobject]@{ LauncherProcessId = $server.Id; Endpoint = "http://127.0.0.1:$Port/v1/systemone"; Model = 'kev-latest' }
    } else {
        & $pythonPath @nativeArgs
        if ($LASTEXITCODE -ne 0) { throw "Kev exited with code $LASTEXITCODE." }
    }
} finally {
    foreach ($name in $priorEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $priorEnvironment[$name], 'Process')
    }
}
