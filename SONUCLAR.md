# Kripto V1 — ilk çalışma sonuçları

**Sistem kuruldu ve paper takibi başlatıldı. Bu sonuçlarla gerçek işleme geçiş uygun değil.**

Binance'in herkese açık verisinden 50 spot USDT paritesi seçildi; 90 günlük veri indirildi. İlk 200 mum göstergeler için ayrıldı. Yeni listelenen paritelerde geçmiş daha kısa: 200 mum eşiğini karşılamayan coin işlem üretmez. Başlangıç portföyü her bağımsız testte 10.000 USDT.

İşlem dönemi: **15.06.2026 15:45 UTC – 11.09.2026 13:45 UTC** (bitiş hariç).

Son %30 dönem: **16.08.2026 04:45 UTC – 11.09.2026 13:45 UTC**. Bu dönem için kurallar değiştirilmedi ve ayrı başlangıç bakiyesi kullanıldı.

| Ölçüt | Tüm dönem | Son %30 | Son %30, 2 kat maliyet |
|---|---:|---:|---:|
| İşlem sayısı | 310 | 129 | 109 |
| Win rate | %31.61 | %39.53 | %28.44 |
| Expectancy / işlem (USDT) | -4.35 | 2.53 | -8.07 |
| Expectancy / işlem (R) | -0.103 | 0.047 | -0.171 |
| Max drawdown | %17.09 | %6.00 | %10.99 |
| Profit factor | 0.690 | 1.188 | 0.530 |
| Net getiri | %-13.50 | %3.27 | %-8.79 |
| Son portföy (USDT) | 8650.31 | 10326.59 | 9120.77 |

Standart maliyet her yönde %0,10 komisyon ve %0,05 olumsuz fiyat kaymasıdır. Stres testinde ikisi de iki katına çıkarıldı. Maliyetler pozisyon büyüklüğü ve 2R hedefini de etkilediği için stres testindeki işlemler birebir aynı değildir. Max drawdown mum kapanışı portföylerinden hesaplanmıştır.

## Değerlendirme

Tüm dönemin beklentisi negatif. Son dönemin küçük pozitif sonucu maliyet artışına dayanamadı. Mevcut kurallar kârlılığı doğrulanmış bir strateji olarak sunulamaz. Parametreler sonuçlara bakılarak optimize edilmedi.

Evren bugünün 24 saat hacmine göre seçilmiştir; geçmiş tarihlerdeki gerçek ilk 50 listesini yeniden kurmaz. Bu seçim ve hayatta kalma yanlılığıdır. Holdout ayrımı bu yanlılığı gidermez.

## Çalışan paper sistemi

Yerel arka plan takibi başlatıldı ve ilk kayıt doğrulandı: **10000.00 sanal USDT, 0 kapanmış işlem**. Bu bir başlangıç anı kaydıdır; güncel durum [paper raporunda](reports/paper/RAPOR.md) yer alır. Gerçek Binance emri gönderilmez. Bilgisayar açık ve internete bağlı kalmalıdır.

Paper, kapanmış 15 dakikalık mumlarla sanal dolum üretir; gerçek zamanlı borsa dolumunu ölçmez. En az 30 gün / 100 kapanmış işlem öncesinde yeterlilik değerlendirmesi yapılmaz; bu sayılara ulaşmak otomatik onay anlamına gelmez. V1'de canlı emir kodu yoktur.

Telegram modülü yerel bildirim kuyruğu üretir. Telegram'a mesaj gönderimi bağlı değildir.

## Dosyalar ve kontrol

- [Kullanım ve strateji kuralları](README.md)
- [Tam dönem ayrıntılı rapor](reports/backtest/full/RAPOR.md)
- [Son dönem raporu](reports/backtest/holdout/RAPOR.md)
- [Maliyet stresi raporu](reports/cost_stress/holdout/RAPOR.md)
- [İşlem dökümü](reports/backtest/full/trades.csv)
- [Sanal takip durdurma](Durdur.ps1) ve [yeniden başlatma](Baslat.ps1)

18 otomatik test geçti. Ayrıca sabit backtest dönemi yeniden çalıştırıldı ve sonuçların birebir tekrarlandığı doğrulandı. Risk bütçesi maliyetleri içerir; fiyat boşluklarında gerçekleşen zarar planlanan %0,5'i aşabilir. Günlük kesici %2 veya art arda 3 zarardır; en fazla 3 eşzamanlı pozisyon vardır.
