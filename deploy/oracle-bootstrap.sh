#!/usr/bin/env bash
# One-shot installer for running the live service on a plain Ubuntu VM
# (written for Oracle Cloud Always Free, works on any Debian/Ubuntu box).
#
# Why a VM at all: Render's free tier shares one egress IP between many
# customers, and on 18 Sep 2026 Binance banned that IP (HTTP 418, -1003)
# for ~9 hours -- for all of Render's tenants, not for anything this bot
# did. A VM has its own IP, never sleeps, and needs no wake-up ping, so
# render_web's own 5-minute loop does everything on its own.
#
# What it does (idempotent -- safe to re-run):
#   1. installs docker + git
#   2. clones/updates the repo into /opt/kripto/src
#   3. asks for the secrets ONCE and stores them root-only under
#      /opt/kripto/secrets (the Ed25519 key is multi-line, which is why
#      this is not a plain .env file)
#   4. writes /opt/kripto/run.sh (build + (re)start the container) and
#      /opt/kripto/update.sh (git pull + run.sh) and runs run.sh
#
# Usage on the VM:
#   curl -fsSL https://raw.githubusercontent.com/ErhanNTRK/kripto-v1-paper/main/deploy/oracle-bootstrap.sh | sudo bash
set -euo pipefail

REPO_URL="https://github.com/ErhanNTRK/kripto-v1-paper.git"
BASE=/opt/kripto
SRC=$BASE/src
SECRETS=$BASE/secrets

if [ "$(id -u)" -ne 0 ]; then
  echo "Bu script root olarak calismali: 'sudo bash' ile calistirin." >&2
  exit 1
fi

echo "==> Paketler (docker, git)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq docker.io git curl >/dev/null
systemctl enable --now docker >/dev/null

echo "==> Kaynak kod: $SRC"
mkdir -p "$BASE"
if [ -d "$SRC/.git" ]; then
  git -C "$SRC" fetch -q origin main && git -C "$SRC" reset -q --hard origin/main
else
  git clone -q "$REPO_URL" "$SRC"
fi

mkdir -p "$SECRETS"
chmod 700 "$SECRETS"

ask_secret() {  # ask_secret FILE PROMPT [optional]
  local file="$SECRETS/$1" prompt="$2" optional="${3:-}"
  if [ -s "$file" ]; then
    echo "   $1: mevcut deger korunuyor (degistirmek icin dosyayi silin: $file)"
    return
  fi
  local value
  while true; do
    read -r -p "   $prompt: " value </dev/tty
    if [ -n "$value" ] || [ -n "$optional" ]; then break; fi
    echo "   Bos birakilamaz."
  done
  printf '%s' "$value" > "$file"
  chmod 600 "$file"
}

ask_multiline_secret() {  # the PEM key: paste all lines, then a line containing only END
  local file="$SECRETS/$1" prompt="$2"
  if [ -s "$file" ]; then
    echo "   $1: mevcut deger korunuyor (degistirmek icin dosyayi silin: $file)"
    return
  fi
  echo "   $prompt"
  echo "   (Tum satirlari yapistirin -- '-----BEGIN PRIVATE KEY-----' dan '-----END PRIVATE KEY-----' a kadar --"
  echo "    sonra tek basina END yazip Enter'a basin)"
  : > "$file"
  chmod 600 "$file"
  local line
  while IFS= read -r line </dev/tty; do
    [ "$line" = "END" ] && break
    printf '%s\n' "$line" >> "$file"
  done
  if ! grep -q "BEGIN PRIVATE KEY" "$file"; then
    echo "   Anahtar 'BEGIN PRIVATE KEY' icermiyor; tekrar deneyin." >&2
    rm -f "$file"
    exit 1
  fi
}

echo "==> Gizli bilgiler (Render'daki Environment sekmesindeki degerlerin aynisi)"
ask_secret BINANCE_API_KEY "BINANCE_API_KEY"
ask_multiline_secret BINANCE_ED25519_PRIVATE_KEY "BINANCE_ED25519_PRIVATE_KEY (PEM)"
ask_secret TELEGRAM_BOT_TOKEN "TELEGRAM_BOT_TOKEN"
ask_secret TELEGRAM_CHAT_ID "TELEGRAM_CHAT_ID (sayi)"
ask_secret TELEGRAM_WEBHOOK_SECRET "TELEGRAM_WEBHOOK_SECRET (bos birakilabilir)" optional
if [ ! -s "$SECRETS/LIVE_TRADING_CONFIRMATION" ]; then
  # The second of the two independent real-order switches
  # (live_execution.execution_enabled); the first is live_config.json.
  printf '%s' "ENABLE_KRIPTO_V1_REAL_ORDERS" > "$SECRETS/LIVE_TRADING_CONFIRMATION"
  chmod 600 "$SECRETS/LIVE_TRADING_CONFIRMATION"
fi

cat > "$BASE/run.sh" <<'RUN'
#!/usr/bin/env bash
# Build the image from /opt/kripto/src and (re)start the container.
set -euo pipefail
BASE=/opt/kripto
S=$BASE/secrets
COMMIT=$(git -C "$BASE/src" rev-parse HEAD)
docker build -q -t kripto:latest "$BASE/src" >/dev/null
docker rm -f kripto >/dev/null 2>&1 || true
docker run -d --name kripto --restart unless-stopped \
  -p 127.0.0.1:10000:10000 \
  -e PORT=10000 \
  -e RENDER_GIT_COMMIT="$COMMIT" \
  -e BINANCE_API_KEY="$(cat "$S/BINANCE_API_KEY")" \
  -e BINANCE_ED25519_PRIVATE_KEY="$(cat "$S/BINANCE_ED25519_PRIVATE_KEY")" \
  -e TELEGRAM_BOT_TOKEN="$(cat "$S/TELEGRAM_BOT_TOKEN")" \
  -e TELEGRAM_CHAT_ID="$(cat "$S/TELEGRAM_CHAT_ID")" \
  -e TELEGRAM_WEBHOOK_SECRET="$(cat "$S/TELEGRAM_WEBHOOK_SECRET" 2>/dev/null || true)" \
  -e LIVE_TRADING_CONFIRMATION="$(cat "$S/LIVE_TRADING_CONFIRMATION")" \
  kripto:latest >/dev/null
echo "kripto container started at commit ${COMMIT:0:12}"
RUN
chmod 700 "$BASE/run.sh"

cat > "$BASE/update.sh" <<'UPD'
#!/usr/bin/env bash
# Pull the latest main and restart the container on it.
set -euo pipefail
git -C /opt/kripto/src fetch -q origin main
git -C /opt/kripto/src reset -q --hard origin/main
exec /opt/kripto/run.sh
UPD
chmod 700 "$BASE/update.sh"

echo "==> Konteyner baslatiliyor"
"$BASE/run.sh"

echo "==> Ilk kontrol (10 sn)"
sleep 10
if curl -fsS http://127.0.0.1:10000/health; then
  echo
  echo "KURULUM TAMAM. Faydali komutlar:"
  echo "  sudo docker logs -f kripto            # canli log"
  echo "  curl -s http://127.0.0.1:10000/status # son tick, rate-limit durumu"
  echo "  sudo /opt/kripto/update.sh            # GitHub main'deki son kodu al ve yeniden baslat"
else
  echo
  echo "Servis henuz cevap vermiyor; loglara bakin: sudo docker logs kripto" >&2
  docker logs --tail 40 kripto >&2 || true
  exit 1
fi
