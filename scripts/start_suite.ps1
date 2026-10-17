[CmdletBinding()]
param(
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [ValidateRange(1, 65535)][int]$ClassifierPort = 8081,
    [string]$DataDirectory = '',
    [string]$ModelCache = '',
    [string]$SpeechModel = '',
    [switch]$ReviewedProfile,
    [switch]$FastProfile,
    [switch]$ValidateOnly
)

# Start only the pinned local services. No downloads, paid API, or broad kills.
$ErrorActionPreference = 'Stop'
$localReviewedProfile = $ReviewedProfile -or $FastProfile
$selectedPolicy = if ($FastProfile) { 'fast' } elseif ($ReviewedProfile) { 'scene' } else { 'verified' }
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if (-not $DataDirectory) { $DataDirectory = Join-Path $projectRoot 'data' }
if (-not $ModelCache) { $ModelCache = Join-Path $projectRoot '.local\models\whisper' }
$dataPath = [IO.Path]::GetFullPath($DataDirectory)
$cachePath = [IO.Path]::GetFullPath($ModelCache)
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$modelPath = Join-Path $projectRoot '.local\models\qwen3.8-27b\Qwen3.8-27B-Q4_K_M.gguf'
$binaryPath = Join-Path $projectRoot '.local\llama.cpp\llama-server.exe'
if ($Port -eq $ClassifierPort) { throw 'The application and classifier need different ports.' }
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw 'Create the project Python environment and install the transcription dependencies first.' }
foreach ($binary in @('ffmpeg', 'ffprobe')) {
    if (-not (Get-Command $binary -ErrorAction SilentlyContinue)) { throw "$binary is required on PATH." }
}
if (-not $SpeechModel) {
    $resolveModel = @'
import json,sqlite3,sys
from pathlib import Path
p=Path(sys.argv[1]).resolve()/'library.sqlite3'
model=sys.argv[2]
if p.is_file():
    with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as db:
        row=db.execute("SELECT value FROM settings WHERE key='transcription_model'").fetchone()
        if row: model=json.loads(row[0])
print(model)
'@
    $defaultSpeechModel = if ($localReviewedProfile) { 'large-v3' } else { 'small.en' }
    $SpeechModel = $resolveModel | & $pythonPath - $dataPath $defaultSpeechModel
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the saved speech model.' }
}
$verifySpeech = @'
import importlib.util,sys
from castwell.config import _model_directory
required=('castwell','fastapi','uvicorn','feedparser','requests','multipart','faster_whisper')
if any(importlib.util.find_spec(name) is None for name in required):
    raise SystemExit('Install the application transcription dependencies first.')
if not _model_directory({'transcription_model':sys.argv[1],'model_cache':sys.argv[2]}):
    raise SystemExit('Prepare the selected speech model before starting the suite.')
'@
$verifySpeech | & $pythonPath - $SpeechModel $cachePath
if ($LASTEXITCODE -ne 0) { throw 'The selected speech model or application dependencies are unavailable.' }
& (Join-Path $PSScriptRoot 'start_qwen38_dense_local.ps1') -Port $ClassifierPort -NoThinking -LogVerbosity 4 -ValidateOnly | Out-Null

function Test-Argument([string]$Command, [string]$Name, [string]$Value) {
    $literal = [regex]::Escape($Value)
    $option = [regex]::Escape($Name)
    return [regex]::IsMatch($Command, '(?:^|\s)(?:"' + $option + '"|' + $option + ')\s+(?:"' + $literal + '"|' + $literal + ')(?:\s|$)', 'IgnoreCase')
}
function Get-ProjectListener([string]$Role, [int]$ListenPort) {
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $ListenPort -ErrorAction SilentlyContinue)
    if (-not $listeners.Count) { return $null }
    if (@($listeners | Where-Object { $_.LocalAddress -notin @('127.0.0.1', '::1') }).Count) { throw "Port $ListenPort is exposed beyond loopback; refusing reuse." }
    $processIds = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($processIds.Count -ne 1) { throw "Port $ListenPort has multiple owners; refusing reuse." }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($processIds[0])"
    $command = [string]$process.CommandLine
    $owned = (Test-Argument $command '--host' '127.0.0.1') -and (Test-Argument $command '--port' ([string]$ListenPort))
    if ($Role -eq 'classifier') {
        $owned = $owned -and $process.ExecutablePath -ieq $binaryPath -and (Test-Argument $command '--model' $modelPath) -and (Test-Argument $command '--alias' 'castwell-local')
        foreach ($setting in @(
            @('--n-gpu-layers','all'), @('--fit','off'), @('--reasoning','off'),
            @('--reasoning-budget','0'), @('--n-predict','2048'), @('--temp','0.7'),
            @('--top-p','0.8'), @('--presence-penalty','0'), @('--sleep-idle-seconds','1')
        )) {
            $owned = $owned -and (Test-Argument $command $setting[0] $setting[1])
        }
        if ($FastProfile) { $owned = $owned -and (Test-Argument $command '--ctx-size' '32768') }
    } else {
        $owned = $owned -and $command -match '^(?:"[^"]+"|\S+)\s+(?:-u\s+)?-m\s+castwell(?:\s|$)' -and (Test-Argument $command '--data-dir' $dataPath)
    }
    if (-not $owned) { throw "Port $ListenPort belongs to another service; refusing reuse." }
    return $process
}
Add-Type -AssemblyName System.Net.Http
$httpHandler = [System.Net.Http.HttpClientHandler]::new()
$httpHandler.UseProxy = $false
$httpHandler.AllowAutoRedirect = $false
$httpClient = [System.Net.Http.HttpClient]::new($httpHandler)
$httpClient.Timeout = [TimeSpan]::FromSeconds(4)
function Get-LocalJson([string]$Url) {
    $response = $httpClient.GetAsync($Url).GetAwaiter().GetResult()
    try {
        [void]$response.EnsureSuccessStatusCode()
        return $response.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json
    } finally { $response.Dispose() }
}
function Wait-LocalReady([string]$Url, [string]$Role, [int]$ListenPort) {
    $timer = [Diagnostics.Stopwatch]::StartNew()
    while ($timer.Elapsed.TotalSeconds -lt 180) {
        $healthy = $false
        try {
            $healthy = (Get-LocalJson $Url).status -eq 'ok'
        } catch { }
        if ($healthy) { return Get-ProjectListener $Role $ListenPort }
        Start-Sleep -Milliseconds 500
    }
    throw "$Role did not become ready. Inspect .local\logs and use stop_suite.ps1 before retrying."
}
function Process-Record($Process, [string]$Role, [int]$ListenPort) {
    $bytes = [Text.Encoding]::UTF8.GetBytes([string]$Process.CommandLine)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $hash = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '').ToLowerInvariant() } finally { $sha.Dispose() }
    return @{ role=$Role; port=$ListenPort; pid=$Process.ProcessId; created_utc=$Process.CreationDate.ToUniversalTime().ToString('o'); command_sha256=$hash }
}
$runtimeDir = Join-Path $projectRoot '.local\runtime'
$statePath = Join-Path $runtimeDir "suite-$Port.json"
$state = @{ version=1; data_directory=$dataPath; project_root=$projectRoot; app_url="http://127.0.0.1:$Port"; processes=@() }
try {
    $classifier = Get-ProjectListener 'classifier' $ClassifierPort
    $application = Get-ProjectListener 'app' $Port
    if ($ValidateOnly) {
        [pscustomobject]@{ Validated=$true; StartsServices=$false; SpeechModel=$SpeechModel; DetectorPolicy=$selectedPolicy; SpeechBatchOverride=$(if ($FastProfile) { 8 } else { $null }); ExistingApp=[bool]$application; ExistingClassifier=[bool]$classifier }
        return
    }
    if ($application) {
        $currentSettings = Get-LocalJson "http://127.0.0.1:$Port/api/settings"
        if ($currentSettings.ai_policy -ne $selectedPolicy) {
            throw 'The running application uses another detector profile. Use stop_suite.ps1 before starting this profile.'
        }
        $currentJobs = Get-LocalJson "http://127.0.0.1:$Port/api/jobs"
        $modelPreparation = Get-LocalJson "http://127.0.0.1:$Port/api/models/status"
        if (@($currentJobs.jobs).Count -or $modelPreparation.status -notin @('idle','ready','complete','error')) {
            throw 'Wait for episode processing and speech-model preparation to finish before checking or reusing the classifier.'
        }
    }
    New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
    if (-not $classifier) {
        & (Join-Path $PSScriptRoot 'start_qwen38_dense_local.ps1') -Port $ClassifierPort -NoThinking -LogVerbosity 4 -Background | Out-Null
    }
    $classifier = Wait-LocalReady "http://127.0.0.1:$ClassifierPort/health" 'classifier' $ClassifierPort
    $state.processes += Process-Record $classifier 'classifier' $ClassifierPort
    $state | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $statePath -Encoding UTF8
    if (-not $application) {
        & (Join-Path $PSScriptRoot 'start_castwell.ps1') -Port $Port -ClassifierPort $ClassifierPort -DataDirectory $dataPath -ModelCache $cachePath -SpeechModel $SpeechModel -ReviewedProfile:$ReviewedProfile -FastProfile:$FastProfile -Background | Out-Null
    }
    $application = Wait-LocalReady "http://127.0.0.1:$Port/api/health" 'app' $Port
    $state.processes += Process-Record $application 'app' $Port
    $state | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $statePath -Encoding UTF8
    if ($localReviewedProfile) {
        $profileSettings = @{ detector='ai'; ai_policy=$selectedPolicy; ai_reasoning=$false; auto_approve_threshold=0.90 }
        if ($FastProfile) { $profileSettings.review_only=$true; $profileSettings.transcription_batch_size=8 }
        $body = $profileSettings | ConvertTo-Json -Compress
        $content = [System.Net.Http.StringContent]::new($body, [Text.Encoding]::UTF8, 'application/json')
        $request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::new('PATCH'), "http://127.0.0.1:$Port/api/settings")
        $request.Content = $content
        $response = $httpClient.SendAsync($request).GetAwaiter().GetResult()
        try { [void]$response.EnsureSuccessStatusCode() } finally { $response.Dispose(); $request.Dispose() }
    }
    $reportPath = Join-Path $runtimeDir "readiness-$Port.json"
    & $pythonPath (Join-Path $PSScriptRoot 'check_release.py') --app-url "http://127.0.0.1:$Port" --data-dir $dataPath --require-local-ai --output $reportPath
    if ($LASTEXITCODE -ne 0) { throw 'The suite started but release checks found a blocker. Read the readiness report before processing.' }
    [pscustomobject]@{ OperationalReady=$true; RemovalAcceptance='NotMeasured'; Url="http://127.0.0.1:$Port"; RuntimeState=$statePath; ReadinessReport=$reportPath; SpeechModel=$SpeechModel }
} finally { $httpClient.Dispose(); $httpHandler.Dispose() }
