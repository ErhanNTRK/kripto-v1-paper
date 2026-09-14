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
