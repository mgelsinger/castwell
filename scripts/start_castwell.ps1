[CmdletBinding()]
param(
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [ValidateRange(1, 65535)][int]$ClassifierPort = 8081,
    [string]$DataDirectory = '',
    [string]$ModelCache = '',
    [string]$SpeechModel = '',
    [switch]$ReviewedProfile,
    [switch]$Background
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if (-not $DataDirectory) { $DataDirectory = Join-Path $projectRoot 'data' }
if (-not $ModelCache) { $ModelCache = Join-Path $projectRoot '.local\models\whisper' }
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Project Python environment is missing. Create .venv and install Castwell with the transcription extra.'
}
& $pythonPath -c "import importlib.util,sys; missing=[p for p in ['castwell','fastapi','uvicorn','feedparser','requests','multipart','faster_whisper'] if importlib.util.find_spec(p) is None]; print('Missing dependencies: '+', '.join(missing)) if missing else None; sys.exit(bool(missing))"
if ($LASTEXITCODE -ne 0) { throw 'Install project dependencies with .venv\Scripts\python.exe -m pip install -e .[transcription].' }
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
    $defaultSpeechModel = if ($ReviewedProfile) { 'large-v3' } else { 'small.en' }
    $SpeechModel = $resolveModel | & $pythonPath - ([IO.Path]::GetFullPath($DataDirectory)) $defaultSpeechModel
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the saved speech model.' }
}
$portCheck = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
try { $portCheck.Start() } finally { $portCheck.Stop() }
$environment = @{
    PATH = (Join-Path $projectRoot '.local\windows-vendor-cuda\cuda-runtime') + ';' + $env:PATH
    CASTWELL_AI_BASE_URL = "http://127.0.0.1:$ClassifierPort/v1"
    CASTWELL_AI_MODEL = 'castwell-local'
    CASTWELL_AI_POLICY = $(if ($ReviewedProfile) { 'scene' } else { 'verified' })
    CASTWELL_AI_KEY = $null
    CASTWELL_MODEL_CACHE = [System.IO.Path]::GetFullPath($ModelCache)
    CASTWELL_CLASSIFIER_SLEEP_ADDRESS = "http://127.0.0.1:$ClassifierPort/v1"
    NO_PROXY = '127.0.0.1,localhost,::1'
}
# Removal acceptance has not passed. Preserve an explicit saved preference;
# new profiles require manual review. This direct app launcher does not verify
# the live classifier identity; use start_suite for the pinned CUDA service.
if ($ReviewedProfile) {
    $initializeProfile = @'
import sys
from castwell.library import Library
library=Library(sys.argv[1])
profile={'ai_reasoning':False,'detector':'ai','auto_approve_threshold':0.90}
if 'review_only' not in library.get_settings():
    profile['review_only']=True
if 'transcription_model' not in library.get_settings():
    profile['transcription_model']=sys.argv[2]
library.update_settings(profile)
'@
    $initializeProfile | & $pythonPath - ([IO.Path]::GetFullPath($DataDirectory)) $SpeechModel
    if ($LASTEXITCODE -ne 0) { throw 'Could not initialize the reviewed profile preference.' }
    Write-Host 'Ad removal acceptance has not passed. New profiles require manual review. Use start_suite for a verified local CUDA service.'
}
$priorEnvironment = @{}
try {
    foreach ($name in $environment.Keys) {
        $priorEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $environment[$name], 'Process')
    }
    $nativeArgs = @('-u', '-m', 'castwell', '--host', '127.0.0.1', '--port', [string]$Port,
                    '--data-dir', [System.IO.Path]::GetFullPath($DataDirectory), '--model', $SpeechModel)
    Write-Host "Castwell: http://127.0.0.1:$Port"
    if ($Background) {
        $logDirectory = Join-Path $projectRoot '.local\logs'
        New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
        $quotedArgs = $nativeArgs | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }
        $stdoutPath = Join-Path $logDirectory "castwell-$Port.stdout.log"
        $stderrPath = Join-Path $logDirectory "castwell-$Port.stderr.log"
        $server = Start-Process -FilePath $pythonPath -ArgumentList $quotedArgs -WindowStyle Hidden `
            -WorkingDirectory $projectRoot -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath -PassThru
        $server.Id | Set-Content -LiteralPath (Join-Path $logDirectory "castwell-$Port.pid")
        # Windows venv Python may spawn a child; the HTTP listener PID is authoritative.
        [pscustomobject]@{ LauncherProcessId = $server.Id; Url = "http://127.0.0.1:$Port"; StandardOutput = $stdoutPath; StandardError = $stderrPath }
    } else {
        Push-Location -LiteralPath $projectRoot
        try { & $pythonPath @nativeArgs } finally { Pop-Location }
        if ($LASTEXITCODE -ne 0) { throw "Castwell exited with code $LASTEXITCODE." }
    }
} finally {
    foreach ($name in $priorEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $priorEnvironment[$name], 'Process')
    }
}
