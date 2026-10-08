[CmdletBinding()]
param(
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [string]$DataDirectory = (Join-Path $PSScriptRoot '..\data'),
    [string]$ModelCache = (Join-Path $PSScriptRoot '..\.local\models\whisper'),
    [string]$SpeechModel = 'small.en',
    [switch]$Background
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Project Python environment is missing. Create .venv and install Castwell with the transcription extra.'
}
& $pythonPath -c "import importlib.util,sys; missing=[p for p in ['castwell','fastapi','uvicorn','feedparser','requests','multipart','faster_whisper'] if importlib.util.find_spec(p) is None]; print('Missing dependencies: '+', '.join(missing)) if missing else None; sys.exit(bool(missing))"
if ($LASTEXITCODE -ne 0) { throw 'Install project dependencies with .venv\Scripts\python.exe -m pip install -e .[transcription].' }
foreach ($binary in @('ffmpeg', 'ffprobe')) {
    if (-not (Get-Command $binary -ErrorAction SilentlyContinue)) { throw "$binary is required on PATH." }
}
$portCheck = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
try { $portCheck.Start() } finally { $portCheck.Stop() }
$environment = @{
    CASTWELL_AI_BASE_URL = 'http://127.0.0.1:8081/v1'
    CASTWELL_AI_MODEL = 'castwell-local'
    CASTWELL_AI_POLICY = 'verified'
    CASTWELL_AI_KEY = $null
    CASTWELL_MODEL_CACHE = [System.IO.Path]::GetFullPath($ModelCache)
    NO_PROXY = '127.0.0.1,localhost,::1'
}
# Review mode is read from the library. A new library defaults to review only;
# this launcher never writes or overrides an existing user's saved choice.
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
