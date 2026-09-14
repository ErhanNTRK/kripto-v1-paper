# Kripto V1 araştırma kararı

## Mevcut kanıt

V1'in 90 günlük tam dönem testi negatiftir: 310 işlem, %31,61 kazanma oranı,
-0,103R beklenti, 0,690 profit factor, %17,09 maksimum düşüş ve %-13,50 net
getiri. Son %30 dönemindeki küçük artı sonuç iki kat maliyet testinde negatife
dönmüştür. Bu nedenle gerçek emir kilidi açılmayacaktır.

## Araştırmadan çıkan yön

- Andrea Barbon'un *Catching Crypto Trends* çalışması, hayatta kalma yanlılığı
  giderilmiş bir evrende en likit 20 coin, farklı Donchian dönemlerinin birleşimi
  ve volatiliteye göre pozisyon büyüklüğü kullanıyor. V1 için ilk araştırma
  adayı budur: daha dar ve likit evren, daha düşük işlem sıklığı, tek bir
  breakout eşiği yerine önceden belirlenmiş Donchian birleşimi.
- Kim ve Lim'in *From Predictability to Tradability* çalışması, tahmin başarısı
  ile masraflar sonrası işlem başarısını ayırıyor; birden fazla kayan dönem,
  işlem maliyeti ve gerçek uygulama zamanlaması istiyor. Yeni adaylar yalnızca
  kayan dönem dışı testle değerlendirilecektir.
- Bitcoin likiditesi araştırmaları, spread ve derinliğin zamana ve işlem yerine
  göre değiştiğini gösteriyor. Sabit kayma varsayımı yeterli kanıt değildir;
  paper aşamasında gerçek alış-satış farkı ve gerçekleşen kayma kaydedilmelidir.

Kaynaklar:

- https://www.abarbon.com/papers/catching-crypto-trends
- https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7115197
- https://www.sciencedirect.com/science/article/pii/S0165176518302921
- https://www.sciencedirect.com/science/article/pii/S0165176518301320

## Önceden belirlenmiş test sırası

1. Mevcut 15 dakikalık V1 kontrol modeli olarak korunur.
2. En likit 20 coin üzerinde Donchian birleşimi ve volatilite boyutlandırması
   ayrı aday olarak kurulur.
3. Her iki model 15 dakika ve 1 saatlik mumlarda karşılaştırılır. 1 saatlik
   sürüm, işlem sayısını ve maliyet hassasiyetini azaltıp azaltmadığı için
   incelenir.
4. Ayarlar yalnızca geliştirme penceresinde seçilir; sonraki pencere ayar
   değiştirilmeden test edilir. Dönemler ileri doğru kaydırılır.
5. Normal maliyet, iki kat maliyet ve gecikmeli giriş senaryoları birlikte
   raporlanır. Tarihsel evren yeniden kurulamadığında sonuç açıkça yanlı kabul
   edilir.

Canlı pilot için değişmeyen eşik: en az 30 gün ve 100 kapanmış paper işlem,
pozitif dış dönem beklentisi, profit factor > 1,2, maksimum düşüş <%10 ve iki
kat maliyette pozitif sonuç. Tek bir iyi dönem veya tek bir coin bu eşiği
geçmiş sayılmaz.

## V2 ilk deney sonucu

Önceden belirlenen saatlik Donchian 20/40/80 birleşimi mevcut 90 günlük veri
üzerinde çalıştırıldı. Bugünkü hacim sırasının geçmişe uygulanması nedeniyle
evren yanlılığı devam eder; 20 adayın 18'i yeterli geçmişe sahipti.

| Ölçüt | Tüm dönem | Geliştirme | Son %30 | Son %30, 2 kat maliyet |
|---|---:|---:|---:|---:|
| İşlem | 110 | 63 | 48 | 45 |
| Win rate | %38,18 | %42,86 | %37,50 | %35,56 |
| Expectancy (R) | 0,0226 | 0,0486 | 0,0329 | -0,0800 |
| Max drawdown | %5,95 | %5,95 | %3,37 | %4,26 |
| Profit factor | 1,079 | 1,212 | 1,118 | 0,719 |
| Net getiri | %1,09 | %1,46 | %0,73 | %-1,82 |

V2, V1'den daha iyi olmakla birlikte kabul eşiğini geçmedi. Özellikle tüm
dönem profit factor 1,2'nin altında ve iki kat maliyet beklentisi negatiftir.
Bu sürüm Telegram paper stratejisine veya gerçek emre alınmadı. Daha uzun veri
indirme denemesi TLS zaman aşımında kesildi; veri tamamlanmadan sonuç hakkında
ek çıkarım yapılmadı.

## V2 bir yıllık bulut testi

Yerel TLS sorunu üzerine aynı önceden belirlenmiş model GitHub Actions üzerinde
365 günlük veriyle çalıştırıldı. Çalışma başarıyla tamamlandı ve ham JSON raporu
GitHub çalışma kaydı 34869305279 altında artifact olarak saklandı.

| Ölçüt | Tüm dönem | Geliştirme | Son %30 | Son %30, 2 kat maliyet |
|---|---:|---:|---:|---:|
| İşlem | 266 | 168 | 98 | 98 |
| Win rate | %35,71 | %35,12 | %36,73 | %31,63 |
| Expectancy (R) | 0,0211 | -0,0142 | 0,0816 | -0,0134 |
| Max drawdown | %4,98 | %4,34 | %4,98 | %6,82 |
| Profit factor | 1,080 | 0,936 | 1,340 | 0,941 |
| Net getiri | %2,56 | %-1,32 | %3,92 | %-0,78 |

Son %30 güçlü görünse de geliştirme dönemi ve iki kat maliyet testi negatiftir.
Tam dönem profit factor da 1,2 eşiğini geçmemiştir. Bu, piyasa rejimine ve
maliyet varsayımına duyarlı bir modeldir; gerçek para veya ana paper stratejisi
olarak kabul edilmedi.
