param([switch]$Background, [switch]$Stop)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$serverPath = Join-Path $projectRoot '.runtime\llama-b11960-cpu\llama-server.exe'
$pidPath = Join-Path $projectRoot '.runtime\server.pid'
if ($Stop) {
    if (!(Test-Path -LiteralPath $pidPath)) { throw 'No project-owned server PID file.' }
    $serverProcessId = [int](Get-Content -LiteralPath $pidPath -Raw)
    $serverProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $serverProcessId"
    if (!$serverProcess -or $serverProcess.ExecutablePath -ne $serverPath) {
        throw 'PID does not identify this project server; no process stopped.'
    }
    Stop-Process -Id $serverProcessId
    Write-Output 'Project-owned local inference server stopped.'
    exit
}
$settingsPath = Join-Path $projectRoot 'settings.local.json'
if (!(Test-Path -LiteralPath $serverPath) -or !(Test-Path -LiteralPath $settingsPath)) {
    throw 'Run python -m risk_text_pilot bootstrap --model-dir MODEL_DIRECTORY first.'
}
$localSettings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
$keyPath = Join-Path $projectRoot '.runtime\inference.key'
if (!(Test-Path -LiteralPath $keyPath -PathType Leaf)) {
    throw 'Rerun python -m risk_text_pilot bootstrap to create the project-local inference key.'
}
$modelPath = [string]$localSettings.model_path
if (!(Test-Path -LiteralPath $modelPath -PathType Leaf) -or $modelPath.Contains('"')) {
    throw 'Invalid model path.'
}
if (Get-NetTCPConnection -State Listen -LocalPort 18080 -ErrorAction SilentlyContinue) {
    throw 'Port 18080 is already occupied; existing service was not changed.'
}
$serverArguments = @('-m', $modelPath, '--host', '127.0.0.1', '--port', '18080',
    '-np', '1', '-c', '4096', '-t', '6', '--gpu-layers', '0',
    '--alias', 'qwen3-4b-pilot', '--reasoning-budget', '0',
    '--cors-origins', 'localhost', '--api-key-file', 'inference.key')
# Native key-file loading does not support Chinese absolute paths; use an ASCII relative path.
$runtimeWorkingDirectory = Join-Path $projectRoot '.runtime'
if ($Background) {
    $quotedServerArguments = $serverArguments | ForEach-Object { '"' + $_.Replace('"', '\"') + '"' }
    $serverProcess = Start-Process -FilePath $serverPath -ArgumentList $quotedServerArguments -WorkingDirectory $runtimeWorkingDirectory -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $projectRoot '.runtime\server.stdout.log') `
        -RedirectStandardError (Join-Path $projectRoot '.runtime\server.stderr.log')
    $serverProcess.Id | Set-Content -LiteralPath $pidPath -Encoding ascii
    $readyDeadline = [DateTime]::UtcNow.AddSeconds(30)
    while ($true) {
        $serverProcess.Refresh()
        if ($serverProcess.HasExited) { throw 'Server exited; inspect .runtime/server.stderr.log.' }
        if ([DateTime]::UtcNow -gt $readyDeadline) { throw 'Server not ready within 30 seconds; inspect logs before starting again.' }
        $healthResponse = $null
        try {
            $healthRequest = [System.Net.HttpWebRequest]::Create('http://127.0.0.1:18080/health')
            $healthRequest.Proxy = $null
            $healthRequest.Timeout = 1500
            $healthRequest.Headers.Add('Authorization', ('Bearer ' + (Get-Content -LiteralPath $keyPath -Raw).Trim()))
            $healthResponse = $healthRequest.GetResponse()
            if ([int]$healthResponse.StatusCode -eq 200) { break }
        } catch [System.Net.WebException] {
            # Loading-model 503 and connection-refused are expected briefly during startup.
        } finally {
            if ($healthResponse) { $healthResponse.Close() }
        }
        Start-Sleep -Milliseconds 500
    }
    Write-Output "Local-only server started, PID $($serverProcess.Id), port 18080. Logs: .runtime"
} else {
    Push-Location $runtimeWorkingDirectory
    try { & $serverPath @serverArguments } finally { Pop-Location }
}
