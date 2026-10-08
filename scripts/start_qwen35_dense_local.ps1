[CmdletBinding()]
param(
    [string]$Model = (Join-Path $PSScriptRoot '..\.local\models\qwen3.5-27b\Qwen3.5-27B-Q4_K_M.gguf'),
    [string]$ServerBinary = (Join-Path $PSScriptRoot '..\.local\llama.cpp\llama-server.exe'),
    [ValidateRange(1, 65535)][int]$Port = 8081,
    [ValidateRange(1, 128)][int]$Threads = 8,
    [ValidateRange(4096, 32768)][int]$Context = 8192,
    [ValidatePattern('^(auto|all|[0-9]{1,3})$')][string]$GpuLayers = 'all',
    [ValidateRange(1, 16384)][int]$MaxTokens = 4096,
    [ValidateRange(0, 8192)][int]$ReasoningBudget = 1024,
    [ValidateRange(0, 5)][int]$LogVerbosity = 3,
    [switch]$NoThinking,
    [switch]$Background,
    [switch]$ValidateOnly
)

# A separate dense-model launcher. It never downloads or stops another server.
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
if ((Get-Item -LiteralPath $modelPath).Length -ne 16740812704 -or
    (Get-FileHash -LiteralPath $modelPath -Algorithm SHA256).Hash -ne '84b5f7f112156d63836a01a69dc3f11a6ba63b10a23b8ca7a7efaf52d5a2d806') {
    throw 'The model must match the pinned Unsloth Qwen3.5-27B Q4_K_M artifact. Existing files are unchanged.'
}
if ((Get-FileHash -LiteralPath $binaryPath -Algorithm SHA256).Hash -ne '7b886298b688509ced3e92b420edd57dd3d665da72c1fc207d7a537be5870352') {
    throw 'Expected the Windows x64 llama.cpp b11146 server entrypoint and its matching runtime libraries.'
}

$thinkingMode = if ($NoThinking) { 'off' } else { 'on' }
$temperature = if ($NoThinking) { '0.7' } else { '1.0' }
$topP = if ($NoThinking) { '0.8' } else { '0.95' }
$nativeArgs = @(
    '--model', $modelPath, '--alias', 'castwell-local', '--offline',
    '--host', '127.0.0.1', '--port', [string]$Port,
    '--threads', [string]$Threads, '--threads-batch', [string]$Threads,
    '--ctx-size', [string]$Context, '--parallel', '1',
    '--n-gpu-layers', $GpuLayers, '--fit', 'on', '--fit-target', '1536',
    '--batch-size', '512', '--ubatch-size', '128', '--flash-attn', 'on',
    '--jinja', '--reasoning', $thinkingMode, '--reasoning-format', 'deepseek',
    '--reasoning-budget', [string]$effectiveReasoningBudget, '--n-predict', [string]$MaxTokens,
    '--temp', $temperature, '--top-p', $topP, '--top-k', '20',
    '--min-p', '0', '--presence-penalty', '1.5', '--repeat-penalty', '1.0',
    '--seed', '42', '--log-verbosity', [string]$LogVerbosity
)

if ($ValidateOnly) {
    [pscustomobject]@{ ServerBinary = $binaryPath; Arguments = $nativeArgs; StartsServer = $false }
    return
}
$portCheck = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
try {
    $portCheck.Start()
} catch {
    throw "Port $Port is occupied. Stop your previous local server or choose -Port."
} finally {
    $portCheck.Stop()
}

function ConvertTo-NativeArgument([string]$Value) {
    # Windows native quoting preserves spaces, quotes and trailing backslashes.
    $quoted = [System.Text.StringBuilder]::new()
    [void]$quoted.Append('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') {
            $slashes++
        } elseif ($character -eq '"') {
            [void]$quoted.Append(('\' * (2 * $slashes + 1)))
            [void]$quoted.Append('"')
            $slashes = 0
        } else {
            [void]$quoted.Append(('\' * $slashes))
            [void]$quoted.Append($character)
            $slashes = 0
        }
    }
    [void]$quoted.Append(('\' * (2 * $slashes)))
    [void]$quoted.Append('"')
    return $quoted.ToString()
}

Write-Host "Castwell AI endpoint: http://127.0.0.1:$Port/v1"
Write-Host 'Model alias: castwell-local; published Unsloth Qwen3.5-27B Q4_K_M'
if ($Background) {
    $logDirectory = Join-Path $projectRoot '.local\logs'
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $logStem = "qwen35-dense-$Port-" + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfff')
    $stdoutPath = Join-Path $logDirectory "$logStem.stdout.log"
    $stderrPath = Join-Path $logDirectory "$logStem.stderr.log"
    $argumentLine = ($nativeArgs | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' '
    $serverProcess = Start-Process -FilePath $binaryPath -ArgumentList $argumentLine `
        -WorkingDirectory (Split-Path -Parent $binaryPath) -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
    $pidPath = Join-Path $logDirectory "$logStem.pid"
    $serverProcess.Id | Set-Content -LiteralPath $pidPath
    [pscustomobject]@{
        ProcessId = $serverProcess.Id
        Endpoint = "http://127.0.0.1:$Port/v1"
        Model = 'castwell-local'
        ProcessIdFile = $pidPath
        StandardOutput = $stdoutPath
        StandardError = $stderrPath
    }
} else {
    & $binaryPath @nativeArgs
    exit $LASTEXITCODE
}
