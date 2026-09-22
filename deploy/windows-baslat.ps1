# Botu kendi Windows bilgisayarinda calistirir (Render'in paylasimli IP'si
# Binance tarafindan banlandiginda yedek/alternatif; ev interneti IP'si
# sadece size ait). Ayni kodu, ayni ayarlarla calistirir -- Render ile
# AYNI ANDA calistirmayin (Render'da servisi Suspend edin ya da ban bitene
# kadar Render zaten Binance'e dokunmuyor).
#
# Ilk calistirma: PowerShell'i acip su komutu yapistirin
#   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force
#   irm https://raw.githubusercontent.com/ErhanNTRK/kripto-v1-paper/main/deploy/windows-baslat.ps1 | iex
# Sonraki calistirmalar: masaustundeki "Kripto Bot.bat" dosyasina cift tiklayin.
$ErrorActionPreference = "Stop"

$Base = Join-Path $env:USERPROFILE "kripto"
$Src = Join-Path $Base "src"
$Secrets = Join-Path $Base "secrets"
$RepoUrl = "https://github.com/ErhanNTRK/kripto-v1-paper.git"

function Need-Cmd($name, $hint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        Write-Host "HATA: '$name' bulunamadi. $hint" -ForegroundColor Red
        Write-Host "Kurduktan sonra bu pencereyi kapatip scripti yeniden calistirin."
        Read-Host "Kapatmak icin Enter"
        exit 1
    }
}
Need-Cmd git "https://git-scm.com/download/win adresinden Git'i kurun (varsayilan ayarlar)."
Need-Cmd python "https://www.python.org/downloads/ adresinden Python 3.12'yi kurun; kurulumda 'Add python.exe to PATH' kutusunu isaretleyin."

New-Item -ItemType Directory -Force -Path $Base, $Secrets | Out-Null

Write-Host "==> Kaynak kod: $Src"
if (Test-Path (Join-Path $Src ".git")) {
    git -C $Src fetch -q origin main
    git -C $Src reset -q --hard origin/main
} else {
    git clone -q $RepoUrl $Src
}

Write-Host "==> Python ortami"
$Venv = Join-Path $Base "venv"
if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) { python -m venv $Venv }
$Py = Join-Path $Venv "Scripts\python.exe"
& $Py -m pip install -q --upgrade pip
& $Py -m pip install -q -r (Join-Path $Src "requirements.txt")

function Ask-Secret($file, $prompt, [switch]$Optional) {
    $path = Join-Path $Secrets $file
    # An optional secret deliberately left empty must not be asked again:
    # the Read-Host blocked every unattended restart until someone pressed
    # Enter (22 Sep 2026: the bot sat at this prompt through an entry window).
    if ((Test-Path $path) -and (((Get-Item $path).Length -gt 0) -or $Optional)) {
        Write-Host "   $file : mevcut deger korunuyor (degistirmek icin silin: $path)"
        return
    }
    while ($true) {
        $value = Read-Host "   $prompt"
        if ($value -or $Optional) { break }
        Write-Host "   Bos birakilamaz."
    }
    [IO.File]::WriteAllText($path, $value)
}

Write-Host "==> Gizli bilgiler (Render -> kripto-v1-frankfurt -> Environment sekmesindeki degerlerin aynisi)"
Ask-Secret "BINANCE_API_KEY" "BINANCE_API_KEY"
$PemPath = Join-Path $Secrets "BINANCE_ED25519_PRIVATE_KEY"
if (-not (Test-Path $PemPath) -or -not (Select-String -Path $PemPath -Pattern "BEGIN PRIVATE KEY" -Quiet)) {
    Write-Host "   BINANCE_ED25519_PRIVATE_KEY: simdi Not Defteri acilacak. Anahtarin TAMAMINI"
    Write-Host "   (-----BEGIN PRIVATE KEY----- satirindan -----END PRIVATE KEY----- satirina kadar)"
    Write-Host "   yapistirin, kaydedin (Ctrl+S) ve Not Defteri'ni kapatin."
    Read-Host "   Hazir olunca Enter"
    if (-not (Test-Path $PemPath)) { New-Item -ItemType File -Path $PemPath | Out-Null }
    Start-Process notepad.exe -ArgumentList $PemPath -Wait
    if (-not (Select-String -Path $PemPath -Pattern "BEGIN PRIVATE KEY" -Quiet)) {
        Write-Host "HATA: anahtar dosyasinda 'BEGIN PRIVATE KEY' yok. Scripti tekrar calistirin." -ForegroundColor Red
        Read-Host "Kapatmak icin Enter"; exit 1
    }
}
Ask-Secret "TELEGRAM_BOT_TOKEN" "TELEGRAM_BOT_TOKEN"
Ask-Secret "TELEGRAM_CHAT_ID" "TELEGRAM_CHAT_ID (sayi)"
Ask-Secret "TELEGRAM_WEBHOOK_SECRET" "TELEGRAM_WEBHOOK_SECRET (bos birakilabilir)" -Optional
$LivePath = Join-Path $Secrets "LIVE_TRADING_CONFIRMATION"
if (-not (Test-Path $LivePath)) { [IO.File]::WriteAllText($LivePath, "ENABLE_KRIPTO_V1_REAL_ORDERS") }

# Uyku modunu kapat (fis takiliyken): bilgisayar uyursa bot durur.
try { powercfg /change standby-timeout-ac 0 | Out-Null; powercfg /change hibernate-timeout-ac 0 | Out-Null } catch {}

# Masaustu kisayolu: sonraki baslatmalar icin. $PSCommandPath bos olabilir
# (script "irm ... | iex" ile calistirilinca diskte bir dosya olmadigindan),
# bu yuzden yerel bir kopya tutmak yerine .bat her seferinde en guncel
# scripti GitHub'dan indirip calistirir.
$Bat = Join-Path ([Environment]::GetFolderPath("Desktop")) "Kripto Bot.bat"
@"
@echo off
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/ErhanNTRK/kripto-v1-paper/main/deploy/windows-baslat.ps1 | iex"
pause
"@ | Set-Content -Path $Bat -Encoding ASCII

function Read-Secret($file) {
    $p = Join-Path $Secrets $file
    if (Test-Path $p) { return [IO.File]::ReadAllText($p).Trim() } else { return "" }
}
$env:BINANCE_API_KEY = Read-Secret "BINANCE_API_KEY"
$env:BINANCE_ED25519_PRIVATE_KEY = [IO.File]::ReadAllText($PemPath)
$env:TELEGRAM_BOT_TOKEN = Read-Secret "TELEGRAM_BOT_TOKEN"
$env:TELEGRAM_CHAT_ID = Read-Secret "TELEGRAM_CHAT_ID"
$env:TELEGRAM_WEBHOOK_SECRET = Read-Secret "TELEGRAM_WEBHOOK_SECRET"
$env:LIVE_TRADING_CONFIRMATION = Read-Secret "LIVE_TRADING_CONFIRMATION"
$env:PORT = "10000"
$env:RENDER_GIT_COMMIT = (git -C $Src rev-parse HEAD)
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONUTF8 = "1"

Write-Host ""
Write-Host "==> Bot basliyor. Bu pencere ACIK kaldigi surece bot calisir; kapatirsaniz durur." -ForegroundColor Green
Write-Host "    Durum: http://127.0.0.1:10000/status   Saglik: http://127.0.0.1:10000/health"
Write-Host ""
Set-Location $Src
& $Py -m crypto_v1.render_web
