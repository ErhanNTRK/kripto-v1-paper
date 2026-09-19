#!/data/data/com.termux/files/usr/bin/bash
# Botu bir Android telefonda, Termux icinde 7/24 calistirir.
#
# Neden telefon: hep acik, kendi ev Wi-Fi IP'sini kullanir (Render'in
# baskalariyla paylasilan ve Binance tarafindan banlanan IP'si degil),
# ucretsiz. iPhone'da bu mumkun degil (arka planda Python calismaz).
#
# Hazirlik (bir kez):
#   1. F-Droid'den "Termux" ve "Termux:Boot" kur (Play Store surumu eski, calismaz).
#   2. Android Ayarlar -> Uygulamalar -> Termux -> Pil -> "Kisitlama yok"
#      (pil optimizasyonu KAPALI), aksi halde Android geceleri botu oldurur.
#   3. Telefon ev Wi-Fi'sinde ve sarjda kalsin.
#   4. Termux'u ac ve yapistir:
#      curl -fsSL https://raw.githubusercontent.com/ErhanNTRK/kripto-v1-paper/main/deploy/termux-baslat.sh | bash
#
# Sonraki baslatmalar: Termux'ta  ~/kripto/baslat.sh
set -euo pipefail

BASE="$HOME/kripto"
SRC="$BASE/src"
SECRETS="$BASE/secrets"
REPO_URL="https://github.com/ErhanNTRK/kripto-v1-paper.git"

echo "==> Paketler (python, git, cryptography)"
pkg update -y >/dev/null 2>&1 || true
# python-cryptography: Termux'un hazir derlenmis paketi -- pip ile kurmak
# telefonda Rust derleyicisi ister ve saatler surer/basarisiz olur.
pkg install -y python git python-cryptography termux-api >/dev/null

echo "==> Kaynak kod: $SRC"
mkdir -p "$BASE" "$SECRETS"
chmod 700 "$SECRETS"
if [ -d "$SRC/.git" ]; then
  git -C "$SRC" fetch -q origin main && git -C "$SRC" reset -q --hard origin/main
else
  git clone -q "$REPO_URL" "$SRC"
fi

ask_secret() {  # ask_secret FILE PROMPT [optional]
  local file="$SECRETS/$1" prompt="$2" optional="${3:-}"
  if [ -s "$file" ]; then
    echo "   $1: mevcut deger korunuyor (degistirmek icin: rm $file)"
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

ask_multiline_secret() {
  local file="$SECRETS/$1" prompt="$2"
  if [ -s "$file" ] && grep -q "BEGIN PRIVATE KEY" "$file"; then
    echo "   $1: mevcut deger korunuyor (degistirmek icin: rm $file)"
    return
  fi
  echo "   $prompt"
  echo "   (Tum satirlari yapistirin -- BEGIN PRIVATE KEY'den END PRIVATE KEY'e kadar --"
  echo "    sonra tek basina END yazip Enter'a basin)"
  : > "$file"; chmod 600 "$file"
  local line
  while IFS= read -r line </dev/tty; do
    [ "$line" = "END" ] && break
    printf '%s\n' "$line" >> "$file"
  done
  if ! grep -q "BEGIN PRIVATE KEY" "$file"; then
    echo "   Anahtar 'BEGIN PRIVATE KEY' icermiyor; scripti tekrar calistirin." >&2
    rm -f "$file"; exit 1
  fi
}

echo "==> Gizli bilgiler (Render -> Environment sekmesindeki degerlerin aynisi)"
ask_secret BINANCE_API_KEY "BINANCE_API_KEY"
ask_multiline_secret BINANCE_ED25519_PRIVATE_KEY "BINANCE_ED25519_PRIVATE_KEY (PEM)"
ask_secret TELEGRAM_BOT_TOKEN "TELEGRAM_BOT_TOKEN"
ask_secret TELEGRAM_CHAT_ID "TELEGRAM_CHAT_ID (sayi)"
ask_secret TELEGRAM_WEBHOOK_SECRET "TELEGRAM_WEBHOOK_SECRET (bos birakilabilir)" optional
[ -s "$SECRETS/LIVE_TRADING_CONFIRMATION" ] || printf '%s' "ENABLE_KRIPTO_V1_REAL_ORDERS" > "$SECRETS/LIVE_TRADING_CONFIRMATION"
chmod 600 "$SECRETS"/*

cat > "$BASE/baslat.sh" <<'RUN'
#!/data/data/com.termux/files/usr/bin/bash
# Botu baslatir; cokerse 15 sn sonra kendiliginden yeniden baslatir.
BASE="$HOME/kripto"; S="$BASE/secrets"
termux-wake-lock 2>/dev/null || true   # Android CPU'yu uyutmasin
export BINANCE_API_KEY="$(cat "$S/BINANCE_API_KEY")"
export BINANCE_ED25519_PRIVATE_KEY="$(cat "$S/BINANCE_ED25519_PRIVATE_KEY")"
export TELEGRAM_BOT_TOKEN="$(cat "$S/TELEGRAM_BOT_TOKEN")"
export TELEGRAM_CHAT_ID="$(cat "$S/TELEGRAM_CHAT_ID")"
export TELEGRAM_WEBHOOK_SECRET="$(cat "$S/TELEGRAM_WEBHOOK_SECRET" 2>/dev/null || true)"
export LIVE_TRADING_CONFIRMATION="$(cat "$S/LIVE_TRADING_CONFIRMATION")"
export PORT=10000 PYTHONUNBUFFERED=1 PYTHONUTF8=1
export RENDER_GIT_COMMIT="$(git -C "$BASE/src" rev-parse HEAD)"
cd "$BASE/src"
while true; do
  python -m crypto_v1.render_web
  echo "bot durdu, 15 sn sonra yeniden baslatiliyor..."; sleep 15
done
RUN
chmod 700 "$BASE/baslat.sh"

cat > "$BASE/guncelle.sh" <<'UPD'
#!/data/data/com.termux/files/usr/bin/bash
# GitHub main'deki son kodu alir; sonra baslat.sh'i yeniden calistirin.
git -C "$HOME/kripto/src" fetch -q origin main && git -C "$HOME/kripto/src" reset -q --hard origin/main
echo "guncellendi: $(git -C "$HOME/kripto/src" rev-parse --short HEAD)"
UPD
chmod 700 "$BASE/guncelle.sh"

# Termux:Boot kuruluysa telefon yeniden basladiginda bot kendiliginden kalkar.
mkdir -p "$HOME/.termux/boot"
cat > "$HOME/.termux/boot/kripto.sh" <<'BOOT'
#!/data/data/com.termux/files/usr/bin/bash
termux-wake-lock
nohup "$HOME/kripto/baslat.sh" >"$HOME/kripto/bot.log" 2>&1 &
BOOT
chmod 700 "$HOME/.termux/boot/kripto.sh"

echo
echo "KURULUM TAMAM. Botu simdi baslatmak icin:"
echo "   ~/kripto/baslat.sh"
echo "Durum (baska bir Termux sekmesinde):  curl -s http://127.0.0.1:10000/status"
echo "Kod guncelleme:                        ~/kripto/guncelle.sh"
