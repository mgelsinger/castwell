[CmdletBinding()]
param(
    [string]$Directory = (Join-Path $PSScriptRoot '..\.local'),
    [ValidateSet("4b", "9b")][string]$Size = "4b",
    [ValidateRange(1, 65535)][int]$Port = 8083,
    [switch]$Background
)

# Requires scripts/setup_kev_local.py. Uses only pinned, already cached weights.
# Kev and Qwen3 should run separately on a 24 GB GPU.
$ErrorActionPreference = 'Stop'
$localRoot = (Resolve-Path -LiteralPath $Directory).Path
$pythonPath = Join-Path $localRoot 'kev-venv\Scripts\python.exe'
$provenanceName = if ($Size -eq '9b') { 'kev9-model-provenance.json' } else { 'kev-model-provenance.json' }
$logPrefix = if ($Size -eq '9b') { 'kev9' } else { 'kev' }
$expectedRevision = if ($Size -eq '9b') { 'db029f08b290afd9fee4aa4bbcd9ae48602d1eb0' } else { '6cfce5c2fa4b4bd64026336ab649c5ca78857d52' }
$provenance = Get-Content -LiteralPath (Join-Path $localRoot $provenanceName) -Raw | ConvertFrom-Json
$checkpointPath = $provenance[0].path
if ($provenance[0].revision -ne $expectedRevision -or
    $provenance[0].repository -ne "jaredpalmer/kev-$Size" -or
    -not (Test-Path -LiteralPath $pythonPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath (Join-Path $checkpointPath 'head.pt') -PathType Leaf)) {
    throw "Pinned Kev $Size runtime is missing. Run scripts/setup_kev_local.py --size $Size first."
}
$verificationScript = Join-Path $PSScriptRoot 'setup_kev_local.py'
& $pythonPath $verificationScript --verify-only --directory $localRoot --size $Size.ToLowerInvariant()
if ($LASTEXITCODE -ne 0) { throw 'Offline Kev verification failed. No server was started.' }
$portCheck = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
try { $portCheck.Start() } finally { $portCheck.Stop() }
$environment = @{
    HF_HOME = (Join-Path $localRoot 'models\huggingface')
    HF_HUB_CACHE = (Join-Path $localRoot 'models\huggingface\hub')
    HUGGINGFACE_HUB_CACHE = (Join-Path $localRoot 'models\huggingface\hub')
    TRANSFORMERS_CACHE = $null
    HF_HUB_OFFLINE = '1'
    TRANSFORMERS_OFFLINE = '1'
    KEV_BACKEND = 'torch'
    KEV_DTYPE = 'bf16'
    KEV_ATTN = 'sdpa'
    KEV_FUSED = '0'
    KEV_CUDA_GRAPHS = '0'
    KEV_PREFIX_CACHE = '1'
    KEV_PREFIX_MAX_TOKENS = '8192'
    KEV_PREFIX_MIN_TOKENS = $null
    KEV_MERGE = '1'
    KEV_LORA_SCALE = '1'
    KEV_TEMPERATURE = $null
    KEV_API_KEY = $null
    KEV_DATE_FACTS = '0'
    KEV_TRUNCATE_STATES = '0'
}
$priorEnvironment = @{}
try {
    foreach ($name in $environment.Keys) {
        $priorEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $environment[$name], 'Process')
    }
    $nativeArgs = @('-u', '-m', 'kev.serve', '--run', $checkpointPath, '--host', '127.0.0.1', '--port', [string]$Port)
    Write-Host "Local Kev $Size endpoint: http://127.0.0.1:$Port/v1/systemone; model: kev-latest"
    if ($Background) {
        $logDirectory = Join-Path $localRoot 'logs'
        New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
        $quotedArgs = $nativeArgs | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }
        $server = Start-Process -FilePath $pythonPath -ArgumentList $quotedArgs -WindowStyle Hidden `
            -WorkingDirectory $localRoot -RedirectStandardOutput (Join-Path $logDirectory "$logPrefix-$Port.stdout.log") `
            -RedirectStandardError (Join-Path $logDirectory "$logPrefix-$Port.stderr.log") -PassThru
        $server.Id | Set-Content -LiteralPath (Join-Path $logDirectory "$logPrefix-$Port.pid")
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
