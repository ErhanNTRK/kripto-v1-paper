# Devir Notu (Handoff) — 22 Eylül 2026

Bu dosya, bu oturumdaki Claude Code (Sonnet 5) oturumunun projeyi devretmesi
için hazırlandı. Yeni bir lokal Claude Code / geliştirme oturumu buradan
devam edebilsin diye durumu eksiksiz özetler.

## 1) Proje ne yapıyor

`kripto-v1-paper`, Binance Futures üzerinde **gerçek parayla** (canlı,
paper değil) çalışan bir kripto trading botu. İki paralel sinyal sistemi var:

- **4H uzun (long) sistemi** — `short_live_config.json`, momentum kırılımı
  bazlı, kaldıraçlı.
- **2H sistemi** — `short_live_config_2h.json`, ayrı bir mum aralığında
  aynı mantığın türevi.

Kod tabanı önce backtest/araştırma motoru (`crypto_v1/backtest.py`,
`research_v5` vb.) olarak geliştirildi, sonra aynı sinyal mantığı canlı
emir gönderen bir kontrolöre (`live_short_controller.py`,
`live_execution.py`, `live_short_market.py`) taşındı.

## 2) Çalıştırma ortamı

- **Nerede çalışıyor:** Kullanıcının kendi Windows PC'si (ev interneti
  IP'si — Render'ın paylaşımlı IP'si Binance tarafından banlandığı için
  yedek/alternatif olarak PC'ye taşındı). Render (`kripto-v1-frankfurt`)
  şu an suspend durumda olmalı; **PC ile Render aynı anda çalıştırılmamalı**
  (ikisi de aynı Binance hesabına emir gönderir).
- **Başlatma komutu** (PowerShell, yönetici gerekmez):
  ```
  irm https://raw.githubusercontent.com/ErhanNTRK/kripto-v1-paper/main/deploy/windows-baslat.ps1 | iex
  ```
  Bu script: repoyu `%USERPROFILE%\kripto\src` altına klonlar/günceller
  (`origin/main`'e hard-reset eder), venv kurar, gizli anahtarları
  (`%USERPROFILE%\kripto\secrets\*`) ilk seferde sorar ve sonraki
  çalıştırmalarda saklı değerleri kullanır, masaüstüne `Kripto Bot.bat`
  kısayolu bırakır, uyku modunu kapatır, `python -m crypto_v1.render_web`
  ile web sunucusunu (ve arka plan tarama döngüsünü) başlatır.
- **Sonraki çalıştırmalar:** Masaüstündeki `Kripto Bot.bat`'a çift tıkla —
  her seferinde GitHub'daki en güncel `main`'i indirip çalıştırır.
- **Durdurma:** PowerShell penceresinde `Ctrl+C`, ya da pencereyi kapatmak.
  Pencere kapanınca bot tamamen durur (arka planda çalışmaya devam etmez).
- **Durum uçları (bot çalışırken, PC'nin kendisinden):**
  `http://127.0.0.1:10000/status`, `http://127.0.0.1:10000/health`
- **Önemli:** Bu Claude Code (remote/cloud) oturumundan kullanıcının
  PC'sindeki `127.0.0.1` uçlarına **doğrudan erişim yok**. Teşhis için
  her zaman kullanıcının PowerShell çıktısı/`/status` JSON'ı yapıştırması
  gerekiyor.

## 3) Git / GitHub iş akışı

- Repo: `ErhanNTRK/kripto-v1-paper`
- Geliştirme branch'i: `main-87br25` (bu oturumun tüm işi burada yapıldı)
- **main ile main-87br25 şu an birebir aynı** (fark yok, `git diff origin/main origin/main-87br25` boş) — yani tüm PR'lar (#22-#28) merge edilmiş durumda.
- PR akışı: local commit → `git fetch origin main` → `git merge origin/main`
  (squash-merge nedeniyle sık sık divergence oluyor, çözüm daima HEAD/local
  lehine) → testler → push → **draft** PR → `subscribe_pr_activity` → ready
  → CI yeşil olunca squash-merge → `unsubscribe_pr_activity`.
- Test suite: **385 test, hepsi geçiyor** (`python -m pytest -q`).

## 4) Bu oturumda yapılan gerçek değişiklikler (PR #22 → #28, hepsi merge edildi)

| PR | Değişiklik |
|----|------------|
| #22 | `signal_confirmation_expiry_minutes` 10→30, `max_entry_drift_fraction` 0.005→0.01, `pilot_capital_usdt` 34.55 |
| #23 | Long exit'lerde BTC filtresinin yanlışlıkla strict modda çalışması, `/positions` side hatası, futures PNL alanı hatası, manuel-AL etiketleme hatası düzeltildi |
| #24 | Kaldıraç artık stop mesafesinden türetiliyor, 3x/5x kademe bir **tavan** (sabit değil); `committed` sermaye artık margin (`notional/leverage`) olarak sayılıyor, tam notional değil — böylece aynı anda birden fazla pozisyon açılabiliyor |
| #25 | Aday evreni artık Binance Futures'ta gerçekten işlem gören sembollerle kesişiyor (`data.futures_tradable_symbols()`) — **ama bkz. Bölüm 5, madde 2: bu PR'ın öncül varsayımı şüpheli** |
| #26 | 4H uzun tarama sonucu konsola yazdırılıyor (2H/short ile aynı görünürlük) |
| #27 | `auto_enter()`/`telegram()` long adaylarını yanlış config'in (`self.config` yerine `self.short_config`) expiry süresiyle topluyordu — düzeltildi |
| #28 | Giriş penceresi genişletildi (30 dk, %1 drift); önbellekteki (cached) 4H manifest de Futures sembolleriyle filtreleniyor |

Bunların hepsi test kapsamında ve gerçek, doğrulanmış düzeltmeler.

## 5) ÇÖZÜLMEMİŞ / açık sorunlar — bir sonraki oturumun önceliği

### 1. Bu gece (22 Eylül) hiç gerçek emir gerçekleşmedi
- Sistem düzgün tarıyor, aday buluyor ("3 AL_ADAYI", sonra "4 AL_ADAYI" —
  detection döngüsünün canlı ilerlediği teyit edildi), ama `/status`'taki
  `entries.results` sürekli boş kaldı — hiç gerçek Binance emri denenmedi.
- "2H penceresi donmuş" teşhisim (saat 17:17'de `window` alanının
  15:00 TR'de takılı kalması) **yanlış çıktı** — sonraki ekran görüntüsünde
  aynı pencere 3'ten 4 AL_ADAYI'na geçti, yani döngü donmamıştı. Bu teşhisi
  kullanıcıya açıkça geri çektim.
- **Kök neden hâlâ bulunamadı**, ama bu oturumun son turunda kodu (Grep +
  Read ile, tahmin değil) inceleyerek birkaç ihtimal **ekarte edildi**,
  birkaçı da **doğrulandı**:
  - ~~`require_exactly_one_pending_signal`~~ **EKARTE EDİLDİ** — bu ayar
    hiçbir Python dosyasında okunmuyor (sadece JSON config'lerde duruyor,
    ölü/vestigial alan). Asıl mekanizma `live_limits.confirmed_signal`'da
    hardcoded `len(pending) != 1` kontrolü, ama `render_web.auto_enter()`
    her adayı `live_signal.isolate_candidate()` ile TEK TEK izole edip
    öyle onaya sokuyor (`live_short_controller.py:92-109` /
    `render_web.py:270-281`) — yani 3-4 eşzamanlı aday olması bu kontrolü
    tetiklemez, bu yol zaten 18 Eylül'de tasarlanmış ve çalışıyor.
  - `pending_candidates()`'ın okuduğu `state_url`/`state_short_url`
    **DOĞRULANDI** — PC modunda (`render_web.main()`, satır ~711-725)
    bunlar GitHub'daki eski `runtime-state` branch'ine değil, doğrudan
    yerel `runtime/*.json` dosyalarına (`file://` URI) işaret ediyor. Yani
    "auto_enter eski/stale veri okuyor" ihtimali de ekarte edildi.
  - **EN GÜÇLÜ KALAN İPUCU — `crypto_v1/live_signal.py:35-61`
    `pending_candidates()` fonksiyonuna bak:** Bir adayı actionable
    saymak için HEM son 30 dk içindeki bir `AL_ADAYI` event'i HEM de o
    sembolün `pending_buys` sözlüğünde bir kaydı olmasını şart koşuyor
    (`event.get("symbol") in pending`). Konsoldaki "3 AL_ADAYI bulundu"
    satırı ise SADECE event sayısını sayıyor
    (`github_worker._print_4h_long_scan`, satır ~144: sadece
    `type == "AL_ADAYI"` filtreliyor, `pending_buys` üyeliğine hiç
    bakmıyor). **Yani events listesinde aday görünüp `pending_buys`
    sözlüğünde karşılığı olmaması, konsolda "N AL_ADAYI bulundu" yazarken
    `auto_enter()`'ın hiçbir şey bulamamasını tam olarak açıklar.**
    `_write_candidate_state` (`github_worker.py:97-131`) events ve
    pending'i aynı kaynaktan birlikte yazıyor gibi görünse de, 4H uzun
    yolu (`paper-engine`/`backtest.Engine`) ayrı bir mekanizma kullanıyor
    (`_print_4h_long_scan` onu okuyor) — events/pending_buys'ın orada da
    gerçekten senkron kalıp kalmadığı DOĞRULANMADI, bu turda zaman
    yetmedi.
- **Önerilen ilk adım (bir sonraki oturum):** Canlı bir "N AL_ADAYI
  bulundu" anından hemen sonra `runtime/state-relaxed.json` ve
  `runtime/state_2h.json`'ı aç, `events` listesindeki her `AL_ADAYI`
  sembolünün gerçekten `pending_buys` sözlüğünde bir karşılığı olup
  olmadığını tek tek karşılaştır (Bölüm 8'deki teşhis scriptini
  `pending_buys` anahtarlarını `events`'teki sembollerle karşılaştıracak
  şekilde genişlet). Eşleşmiyorsa, sorun `_write_candidate_state`'in (4H
  için paper-engine'in) events/pending_buys'ı senkron yazmamasıdır ve tam
  konumu bulunmuş olur.

### 2. FETUSDT / MARSCOINUSDT Futures listeleme çelişkisi (PR #25)
- PR #25, FETUSDT ve MARSCOINUSDT'nin gerçek bir `-1121 Invalid symbol`
  hatasına dayanarak "Futures'ta yok, sadece Spot'ta var" varsayımıyla
  yazıldı.
- Bu gece canlı `fapi.binance.com/fapi/v1/exchangeInfo` sorgusu FETUSDT,
  MARSCOINUSDT, PUMPUSDT, MUBARAKUSDT'nin **hepsinin şu an**
  `PERPETUAL`/`TRADING` olarak Futures'ta listeli olduğunu gösterdi.
- Bu çelişki **çözülmedi**. İki ihtimal: (a) semboller orijinal hatadan
  sonra Futures'a eklendi, (b) orijinal `-1121` hatası başka bir sebepten
  kaynaklanıyordu (yanlış sembol formatı, o anki API hatası, vb.) ve PR
  #25'in teşhisi baştan yanlıştı. PR #25'in filtresi geri alınmadı,
  sadece bu tutarsızlık not edildi.

### 3. `top_n` (evren büyüklüğü) 100'e çıkarılsın mı?
- Kodda sabit değer yok, generic. Sadece 20 vs 50 walk-forward test edildi
  (`.github/workflows/v5-universe-size-test.yml`). 100'e çıkarmak
  mekanik olarak güvenli ama strateji açısından **doğrulanmadı**.

## 6) Binance hesap ayarları (doğrulanmış)

- **Position Mode: One-way** (Hedge Mode değil) — doğrulandı, `-4061`
  riski yok.
- PC saat senkronu doğru (telefonla eşleşiyor) — saat kayması hipotezi
  ekarte edildi.

## 7) Config dosyaları (mevcut hali, referans için)

`short_live_config.json` (4H):
```json
{
  "live_trading_enabled": true,
  "pilot_capital_usdt": 34.55,
  "risk_per_trade_usdt": 5.0,
  "daily_loss_limit_usdt": 12,
  "pilot_loss_limit_usdt": 15,
  "max_open_positions": 5,
  "max_buys_per_day": 40,
  "trailing_atr": 2.0,
  "btc_filter": "very_loose",
  "telegram_buy_command": "AL",
  "signal_confirmation_expiry_minutes": 30,
  "require_exactly_one_pending_signal": true,
  "automatic_risk_exits": true,
  "profit_exit_confirmation_required": false,
  "max_entry_drift_fraction": 0.01,
  "live_fee_buffer_fraction": 0.001
}
```

`short_live_config_2h.json` (2H): aynı alanlar, `risk_per_trade_usdt: 2.0`,
`daily_loss_limit_usdt: 10`, `max_open_positions: 10`.

## 8) Teşhis için kullanılan PowerShell komutları (kullanıcının PC'sinde çalıştırılır)

Pending buy / AL_ADAYI state'ini oku:
```powershell
@'
import json, datetime
for label, path in [("4H", "runtime/state-relaxed.json"), ("2H", "runtime/state_2h.json")]:
    d = json.load(open(path, encoding="utf-8"))["state"]
    print("===", label, "===")
    print("pending_buys:", d.get("pending_buys"))
    events = [e for e in d.get("events", []) if e.get("type") == "AL_ADAYI"]
    for e in events[-5:]:
        t = datetime.datetime.fromtimestamp(e["time"]/1000, datetime.timezone.utc) + datetime.timedelta(hours=3)
        print(" ", e["symbol"], "-> TR saati:", t.strftime("%Y-%m-%d %H:%M:%S"))
    print("pencere (window):", d.get("window"))
    print()
print("Simdiki TR saati:", (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S"))
'@ | python -
```

Binance Futures'ta bir sembolün gerçekten listeli olup olmadığını kontrol et:
```powershell
@'
import urllib.request, json
data = json.load(urllib.request.urlopen("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=20))
symbols = {s["symbol"]: s for s in data["symbols"]}
for sym in ["FETUSDT", "MARSCOINUSDT", "PUMPUSDT", "MUBARAKUSDT"]:
    s = symbols.get(sym)
    if s:
        print(sym, "-> VAR. contractType=" + s.get("contractType", ""), "status=" + s.get("status", ""))
    else:
        print(sym, "-> YOK (Futures'ta bu isimde kontrat yok)")
'@ | python -
```

**Not:** Windows PowerShell'de `python -c "...\"...\""` gibi ters slash'lı
tırnak kaçışı `SyntaxError: unterminated string literal` ile patlıyor.
Her zaman yukarıdaki gibi here-string (`@'...'@ | python -`) kullan.

## 9) Arka plan otomasyonu

Bu oturumun kendi kendine zamanladığı "Kripto v1: PC canlı sistem kontrolü"
rutini (`paper.yml` beklenmedik schedule run'ı ve Render suspend durumu
kontrolü) **silindi** — bu oturum artık kullanılmayacağı için anlamsız
hale gelirdi. Yeni bir oturumda gerekirse benzer bir rutin
(`create_trigger`) yeniden kurulabilir.

## 10) Önerilen sıradaki adım

Bölüm 5, madde 1'deki `require_exactly_one_pending_signal` şüphesinden
başla: `live_short_controller.py` ve `render_web.py` içinde bu ayarın
okunduğu yeri bul, birden fazla `pending_buys` varken (bu gece olduğu
gibi 3-4 tane) gerçek girişin neden hiç denenmediğini uçtan uca izle.
Bu, bugünkü tüm gözlemlerle (adaylar sürekli var, giriş asla yok, sayı
3'ten 4'e değişse bile giriş yok) en tutarlı açıklama.
