$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
$bundledPython = 'C:\Users\ASUS-PC\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
$pythonPath = if (Test-Path -LiteralPath $bundledPython) { $bundledPython } elseif ($pythonCommand) { $pythonCommand.Source } else { '' }
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Python 3.11 veya ustunu kurun.' }
$paperDir = Join-Path $projectDir 'paper'
New-Item -ItemType Directory -Path $paperDir -Force | Out-Null
$processFile = Join-Path $paperDir 'process.json'
if (Test-Path -LiteralPath $processFile) {
    $savedProcess = Get-Content -LiteralPath $processFile -Raw | ConvertFrom-Json
    $existingProcess = Get-Process -Id $savedProcess.pid -ErrorAction SilentlyContinue
    if ($existingProcess -and $existingProcess.StartTime.ToUniversalTime().ToString('o') -eq $savedProcess.started) { throw 'Paper zaten calisiyor.' }
}
$newProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-m','crypto_v1','paper','--watch') -WorkingDirectory $projectDir -WindowStyle Hidden -RedirectStandardOutput (Join-Path $paperDir 'output.log') -RedirectStandardError (Join-Path $paperDir 'error.log') -PassThru
@{pid=$newProcess.Id; started=$newProcess.StartTime.ToUniversalTime().ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $processFile
Write-Output "Paper baslatildi. Surec: $($newProcess.Id)"
