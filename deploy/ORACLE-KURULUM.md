# Oracle Cloud Always Free'ye Taşıma

Neden: Render'ın ücretsiz planı çıkış IP'sini onlarca müşteriyle paylaşır;
18 Eylül 2026'da Binance o IP'yi (74.220.51.139) ~9 saat banladı (HTTP 418).
Kendi IP'si olan bir sunucuda bu sorun yoktur, sunucu uyumaz ve GitHub
Actions'ın "uyandırma" pingine ihtiyaç kalmaz — `render_web`'in kendi
5 dakikalık döngüsü her şeyi yapar.

## 1. Sunucuyu oluştur (Oracle konsolu, ~5 dk)

1. https://cloud.oracle.com → **Compute → Instances → Create instance**
2. **Image:** Canonical Ubuntu 22.04 veya 24.04
3. **Shape:** "Always Free-eligible" olan — `VM.Standard.E2.1.Micro` (AMD) ya da
   `VM.Standard.A1.Flex` (Ampere, 1 OCPU / 6 GB). Frankfurt'ta A1 için
   "Out of capacity" görürsen E2.1.Micro'yu seç (bot için fazlasıyla yeter).
4. **Networking:** varsayılan VCN, **"Assign a public IPv4 address"** işaretli.
5. **SSH keys:** "Generate a key pair for me" → private key'i indir
   (`ssh-key-….key`). Kaybetme.
6. **Create**. 1-2 dk sonra durum **Running**, sağda **Public IP** görünür.

Gelen port açmana gerek yok (servis dışarıya 22 dışında port açmıyor).

## 2. Bağlan

- En kolayı: instance sayfasında **Console connection → Launch Cloud Shell
  connection** (tarayıcıdan terminal), ya da
- Kendi bilgisayarından: `ssh -i ssh-key-….key ubuntu@<PUBLIC_IP>`

## 3. Tek komutla kur

```bash
curl -fsSL https://raw.githubusercontent.com/ErhanNTRK/kripto-v1-paper/main/deploy/oracle-bootstrap.sh | sudo bash
```

Script sırayla sorar (Render → kripto-v1-frankfurt → **Environment**
sekmesindeki değerlerin aynısı):

| Sorulan | Nereden |
|---|---|
| `BINANCE_API_KEY` | Render Environment |
| `BINANCE_ED25519_PRIVATE_KEY` | Render Environment — **tüm satırları** yapıştır, sonra tek başına `END` yazıp Enter |
| `TELEGRAM_BOT_TOKEN` | Render Environment |
| `TELEGRAM_CHAT_ID` | Render Environment (sayı) |
| `TELEGRAM_WEBHOOK_SECRET` | Render Environment (boş bırakılabilir) |

`LIVE_TRADING_CONFIRMATION` otomatik yazılır (gerçek emir anahtarı).

Sonunda `{"ready": true, "binance_connected": true, "orders_enabled": true, ...}`
görürsen kurulum tamam.

## 4. Binance API anahtarında IP kısıtı varsa

Binance → API Management → anahtar → "Restrict access to trusted IPs only"
açıksa sunucunun **Public IP**'sini listeye ekle. Kısıt yoksa bir şey yapma.

## 5. Günlük kullanım

```bash
sudo docker logs -f kripto              # canlı log
curl -s http://127.0.0.1:10000/status   # son tick, rate-limit, red sebepleri
sudo /opt/kripto/update.sh              # GitHub main'deki son kodu al, yeniden başlat
```

Sunucu yeniden başlarsa konteyner kendiliğinden kalkar (`--restart unless-stopped`).

## 6. Render'ı kapat

Sunucu sorunsuz çalıştığını (ilk `ALDIM`/`SATTIM` mesajı ya da `/status`'ta
temiz tick) gördükten sonra Render'daki servisi **Suspend** et: iki kopya aynı
cüzdanda aynı anda çalışmasın.

GitHub Actions'taki `paper.yml` aday üretmeye ve Telegram'a yazmaya aynen devam
eder; sunucu adayları runtime-state branch'inden okur. `paper.yml`'deki Render
uyandırma adımı sunucu için gereksizdir (zararı da yok).
