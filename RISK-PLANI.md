# Kademeli Risk Artis Plani (17 Eylul 2026'da kararlastirildi)

Takvime gore degil, **gerceklesen islem sayisina ve kumulatif net sonuca**
gore ilerler. Otomatik degil -- her checkpoint'te kullaniciya (Erhan) sonuc
raporlanir, onayi alindiktan sonra `live_config.json` guncellenir.

| Asama | Kosul | Sermaye | Islem basi risk |
|---|---|---:|---:|
| 0 (baslangic) | - | 34 USDT | 0.80 USDT |
| 1 | Ilk 15 kapanmis islem, kumulatif net >= 0 | 45 USDT | 1.00 USDT |
| 2 | Sonraki 15 islem, kumulatif net >= 0 | 60 USDT | 1.30 USDT |
| 3 (yuksek risk) | Sonraki 15 islem, kumulatif net >= 0 | 90 USDT | 2.00 USDT |
| Tavan | 3. asamadan sonra | Otomatik ilerlemez, yeniden konusulur |

Guvenlik: her asamada `pilot_loss_limit_usdt` (sermayenin %20'si) zaten
otomatik durduruyor, bu mekanizma degismiyor -- yeni asamaya gecince o limit
de yeni sermayeye gore yeniden hesaplanmali (60 USDT'de %20 = 12 USDT gibi).

Takip: checkpoint'e ne zaman ulasildigini gormek icin Binance emir
gecmisinden (`kv1b`/`kv1s` client ID onekli, gercek islemler) kapanmis
round-trip sayisi ve kumulatif gerceklesen kar/zarar sorgulanir. Bu oturumda
henuz otomatik bir sayac kurulmadi -- Erhan "durumu kontrol et" dedikce
manuel kontrol edilecek; islem hacmi arttikca otomatiklestirilebilir.
