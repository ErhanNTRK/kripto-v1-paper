# Kripto işlem sistemi V1

Binance Spot USDT için backtest, paper takip ve iki ayrı kilitle kapatılmış canlı pilot altyapısı. Frankfurt Render servisi Telegram ve Binance okuma bağlantısıyla çalışır. Gerçek emirler varsayılan olarak kapalıdır; mevcut negatif test sonucu nedeniyle açılmamıştır.

## Başlangıç

Bu klasörde terminal açın:

```text
python -m unittest discover -s tests -v
python -m crypto_v1 fetch --days 90
python -m crypto_v1 backtest
python -m crypto_v1 paper
python -m crypto_v1 paper --watch
```

`paper` tek güncelleme yapar; GitHub Actions 15 dakikada bir paper taraması yapar ve Frankfurt servisini otomatik çıkış kontrolü için uyandırır. Render ücretsiz planı uyuyabildiğinden ilk istek gecikebilir; borsadaki koruyucu stop sunucu uykusundan bağımsızdır.

## Kesin V1 kuralları

- Evren: USDT, işlem gören spot pariteleri; son 24 saat USDT hacmine göre ilk 50. Sabit coin, fiat ve altın tokenleri yapılandırmadaki listeden çıkarılır. Meme coinler için öznel filtre yoktur; yeterince likitse dahil olabilir. BTC de ilk 50 içindeyse işlem görebilir; filtre verisi her zaman alınır.
- Zaman: bütün göstergeler **15 dakika**, yalnızca kapanmış mum. BTC filtresi de 15 dakikadır. Günlük sınır **UTC 00:00**, Türkiye saatiyle 03:00'te sıfırlanır.
- AL: BTC ve coin için kapanış > EMA20 > EMA50 > EMA200; coin RSI(14, Wilder) 55–70; hacim önceki 20 mum ortalamasının en az 1,5 katı; kapanış önceki 20 mumun en yüksek seviyesinin üzerinde. Bütün koşullar zorunludur. RSI/hacim/direnç eşikleri config.json içindedir.
- Sinyal mumu hacim ortalamasına veya dirence dahil edilmez. Giriş sonraki mum açılışı + olumsuz kaymadır. Açılış stopun altındaysa işlem atlanır.
- Stop: `min(sinyal kapanışı − 2 × ATR14, önceki 10 mum desteği − 0,1 × ATR14)`.
- Pozisyon büyüklüğü: portföyün en fazla %0,5'i / birim başına stop zararı; giriş/çıkış komisyonu ve stop kayması dahil. Bakiye ile sınırlı, kaldıraç yok. En fazla 3 pozisyon. Aynı anda adaylar hacim oranına göre sıralanır.
- Hedef: maliyetler sonrası başlangıç riskinin 2 katı net kazancı sağlayan fiyat. Hedefte tüm pozisyon çıkar. Teknik/trailing çıkışlar yüzünden gerçekleşen kazanç 2R'den az olabilir.
- Trailing: fiyat başlangıç risk mesafesinin 1 katı ilerleyince, mum sonunda `en yüksek görülen fiyat − 2 × ATR14`; stop yalnızca yükselir ve sonraki mumda geçerli olur. Aynı mumda yeni stop geriye dönük uygulanmaz.
- SAT: stop/hedef; veya BTC filtresi bozulması, coin kapanışının EMA20 altına düşmesi, RSI<45. Göstergeden SAT sonraki açılışta uygulanır.
- Günlük kesici: gün başı portföyüne göre %2 düşüşte yeni girişleri keser ve açık pozisyonları sonraki açılışta kapatır; açılış boşluğunda kesici görülürse o açılışta kapatır. Üç ardışık zarar yeni girişleri gün sonuna kadar durdurur, mevcut koruyucu çıkışlar devam eder. Gün içinde toparlansa da kesici çözülmez.
- Varsayılan her yön komisyon %0,10; kayma %0,05. Kişisel Binance komisyonunuz ölçülmüş değildir. Stop fiyat boşluğunda planlanan %0,5 zarar sınırı aşılabilir. %2 günlük sınır da garantili gerçekleşme limiti değildir.

## Backtest ve sonuçlar

`reports/backtest/full`, `development`, `holdout` klasörlerinde RAPOR.md, report.json, trades.csv, equity.csv ve yerel telegram_outbox.jsonl oluşur. İlk 200 mum ısınmadır; kalan dönemin son %30'u ayrı, başlangıç bakiyesi sıfırlanmış holdout testidir. Holdout öncesi veriler yalnızca göstergeleri ısıtır. Hiçbir eşik otomatik optimize edilmez. Test sonunda kalan pozisyonlar komisyon/kaymayla kapatılır; bunlar işlem sayısına dahildir.

Raporlar işlem sayısı, win rate, işlem başına net USDT ve R expectancy, mum kapanışlarından max drawdown, profit factor ve net return verir. Zarar eden işlem yoksa profit factor N/A'dır. Açık paper pozisyonları kapanıştan değerlenir; henüz ödenmemiş çıkış komisyonu açık portföy değerinden düşülmez. Win rate/expectancy yalnızca kapanmış işlemlerindir.

**Araştırma sınırlaması:** İlk sürüm bugünün 24 saatlik hacmiyle seçilen 50 coin üzerinde geçmişi test eder. Tarihsel olarak her günün gerçek ilk 50 listesini ve delist edilen coinleri yeniden kurmaz. Bu seçim/hayatta kalma yanlılığıdır; sonuçlar canlı kullanım kanıtı sayılamaz. Evren ve zaman aralığı manifest.json'a kaydedilir. Veri eksikliği veya bozuk mum saptanırsa çalışma hata verir; sessizce fiyat uydurulmaz. Yeni listelenen coinlerde yalnızca mevcut veriler kullanılır ve EMA200 oluşana kadar AL verilmez.

## Paper trading

İlk başlangıçta son kapanmış mumdan başlar; geçmişe dönük sanal kazanç yazmaz. Sonraki kapanışları sırayla işler. Durum atomik kaydedilir, tekrar çağrı aynı mumda işlem çoğaltmaz. Kilit dosyası eşzamanlı yazmayı önler. Beklenmedik kapanışta `.lock` kalmışsa, süreç çalışmadığını doğrulamadan silmeyin. Veri veya ağ hatası süreci durdurur; kayıtlı durum korunur ve yeniden başlatılabilir.

Bu sürüm **mum bazlı paper simülasyonudur**: sonraki mum açılışındaki varsayımsal dolum, o mum kapandıktan sonra görülür; gerçek zamanlı emir defteri, dolum gecikmesi, kısmi dolum ve borsa miktar adımları modellenmez. Kesinti sonrası kaçırılmış mumlar aynı şekilde tamamlanır; bunlar gerçek zamanlı gerçekleşmiş işlemler değildir. Paper raporu bu nedenle gerçek yürütme performansını kanıtlamaz.

Evren başlangıçta sabitlenir. Açık paper takibi sırasında aynı veri klasörüne yeniden `fetch` çalıştırmayın; farklı evren/config için ayrı veri ve state yolu kullanın. `paper/state.json` izleme geçmişini korur. Paper geçmişi yeterlilik incelemesi olmadan silinmemelidir.

## Sonraki aşama için değerlendirme

En az 30 takvim günü ve 100 kapanmış paper işleminden önce karar verilmez. Bunlar istatistiksel başarı garantisi değil asgari inceleme eşiğidir. Pozitif net expectancy, profit factor >1,2, max drawdown <%10, maliyetlerin iki katında dayanıklılık ve farklı piyasa dönemlerinde holdout sonuçları birlikte incelenmelidir. Tarihsel evren yanlılığı ve gerçek zamanlı dolum modeli giderilmeden gerçek emir aşamasına geçilmemelidir. V1 hiçbir koşulda otomatik olarak gerçek işleme geçmez.

## Mimari

`data`: anahtarsız fiyat verisi ve evren; `indicators`: EMA/RSI/ATR; `strategy`: AL/SAT; `risk`: boyut ve hedef; `backtest`: motor ve metrikler; `paper_trading`: kalıcı sanal portföy; `telegram`: bildirim; `live_controller`: yalnızca güncel tek sinyale verilen `AL` onayı; `live_monitor`: otomatik stop, hedef, trailing ve trend çıkışı; `live_market`: Render yeniden başladıktan sonra Binance'ten pilot durumunu kurar. Alımdan hemen sonra koruyucu stop kurulamazsa sistem acil piyasa satışı dener. Ayrıntılar [araştırma kararı](ARASTIRMA.md) ve [Telegram kurulumu](TELEGRAM-KURULUM.md) dosyalarındadır.

Resmi veri kaynağı: https://developers.binance.com/en/docs/products/spot/rest-api — yalnızca https://data-api.binance.vision servisinin time, exchangeInfo, ticker/24hr ve klines uçları kullanılır.
