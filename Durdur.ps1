$ErrorActionPreference = 'Stop'
$processFile = Join-Path $PSScriptRoot 'paper\process.json'
$savedProcess = Get-Content -LiteralPath $processFile -Raw | ConvertFrom-Json
$existingProcess = Get-Process -Id $savedProcess.pid -ErrorAction SilentlyContinue
if ($existingProcess -and $existingProcess.StartTime.ToUniversalTime().ToString('o') -eq $savedProcess.started) {
    New-Item -ItemType File -Path (Join-Path $PSScriptRoot 'paper\stop.request') -Force | Out-Null
    Write-Output 'Guvenli durdurma istendi. Mevcut guncelleme tamamlaninca paper kapanacak.'
} else { Write-Output 'Kayitli paper sureci calismiyor.' }
