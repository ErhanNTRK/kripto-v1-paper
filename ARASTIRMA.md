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

## Çok pencereli (walk-forward) doğrulama — 15 Eylül 2026

Tek bir geliştirme/holdout ayrımının şansa dayanabileceği netleşti (bkz. V1
EMA50 varyantı: tek holdout'ta +%1,42 net getiri, ama o pencere şans çıktı).
Bunun için `crypto_v1/walkforward.py` eklendi: 300-365 günlük veri, 5 bağımsız
ve çakışmayan pencereye bölünüyor, her biri hem normal hem 2 kat maliyetle
sıfırdan test ediliyor. GO kararı yalnızca **tüm pencerelerde** hem normal hem
stres testinde net getiri > 0 ve profit factor >= 1,0 ise veriliyor.

**V1 ailesi (sıkı, trend+EMA50, trend+EMA50+genis trailing) — NO_GO.**
300 gün, 5 pencerenin **hepsinde**, normal maliyette bile net getiri negatif
(-%3 ile -%19 arası), profit factor hep 1,0'ın altında (0,16-0,91). Daha önce
görülen tekil holdout pozitifliği rastlantıymış; bu strateji ailesinde
kanıtlanmış bir kenar (edge) yok.

**V2 (saatlik Donchian 20/40/80, en likit 20 coin) — NO_GO ama çok daha
yakın.** 365 gün, 5 pencere:

| Pencere | İşlem | Net getiri | PF | Stres net getiri | Stres PF |
|---:|---:|---:|---:|---:|---:|
| 0 | 50 | +%0,81 | 1,13 | -%0,50 | 0,93 |
| 1 | 41 | -%2,34 | 0,58 | -%4,23 | 0,35 |
| 2 | 70 | -%1,71 | 0,80 | -%4,29 | 0,53 |
| 3 | 50 | +%2,64 | 1,49 | -%0,90 | 0,86 |
| 4 | 94 | -%0,54 | 0,96 | -%4,41 | 0,67 |

V1'in aksine, **normal maliyette 2/5 pencere gerçekten kârlı** (PF 1,13 ve
1,49), kalan 3'ü de hafif negatif — V1'deki gibi tekdüze kötü değil. Ama **2x
maliyet stresinde 5 pencerenin 5'i de negatife dönüyor**. Yorum: gerçek bir
öngörü gücü (edge) var gibi görünüyor, ama bu edge işlem maliyetlerine göre
ince — güvenli marj yok. Sonraki araştırma yönü: maliyeti düşürmek (daha az
sıklıkta/daha büyük işlem, limit emir/maker ücreti ihtimali, en likit
coin'lere daralt) ya da gerçek Binance maliyetinin 2x varsayımdan daha düşük
olup olmadığını doğrulamak — bu ikisi, parametre ayarlamaya devam etmekten
daha üretken bir yön.

**V3 (6 saatlik, sadece BTC/ETH/SOL)** — 90 günde toplam 11 işlem (holdout'ta
5) üretti, istatistiksel olarak hiçbir sonuç çıkarılamayacak kadar az. Daha
uzun veri ve/veya daha fazla sembol olmadan bu aday değerlendirilemez.

**Bir arkadaşın (bağımsız yazılmış) sinyal botu — karşılaştırma amaçlı.**
Kullanıcının bir arkadaşının kendi yazdığı bot (MA9/21 + RSI 50-68 + hacim
>1,2x, tek pozisyon/8 parite, sabit -%2 stop + %3 başabaş + %5/%3 trailing,
09:00-23:00 TR giriş penceresi) `crypto_v1/friend_v1.py` ile mümkün olduğunca
sadık şekilde tekrar üretildi (5dk yerine 15dk, 1sa aynı — canlı botu 5dk
kullanıyor, elimizdeki en yakın çözünürlük bu) ve aynı 5 pencereli walk-forward
ile test edildi:

| Pencere | İşlem | Net getiri | PF | Stres net getiri | Stres PF | Geçti mi |
|---:|---:|---:|---:|---:|---:|:---:|
| 0 | 23 | +%1,66 | 1,20 | +%0,13 | 1,01 | EVET |
| 1 | 35 | -%1,70 | 0,86 | -%3,90 | 0,71 | hayır |
| 2 | 22 | -%4,95 | 0,38 | -%6,40 | 0,29 | hayır |
| 3 | 28 | -%5,29 | 0,47 | -%6,98 | 0,38 | hayır |
| 4 | 28 | +%2,62 | 1,28 | -%1,12 | 0,91 | hayır |

NO_GO, ama pencere 0 bu araştırmada **hem normal hem 2x maliyet stresini
geçen ilk pencere** oldu (V1 ve V2'nin hiçbir penceresi bunu başaramamıştı).
Ama 4/5 pencere geçmedi, 2'si ciddi kötü (PF 0,38-0,47). Yorum: V1'den daha
gürültülü/tutarsız, kanıtlanmış bir kenar değil — rastgele iyi bir dönem
yakalamış görünüyor. Pozisyon boyutlandırması botun sabit 5 USDC pilot
tavanı yerine özkaynağın %25'i olarak alındı (o tavan küçük pilot sermaye
kısıtıydı, strateji parametresi değil); -%2 stopla bu, bizim
`risk_fraction`imizle aynı %0,5 özkaynak riskine denk geliyor, sonuçlar
karşılaştırılabilir.

**Şu anki durum:** Hiçbir aday (V1 ailesi, V2, arkadaşın botu) gerçek emir
eşiğini geçmedi. En umut verici yön hâlâ V2 — maliyet duyarlılığını
azaltmaya odaklanmak mantıklı sıradaki adım.
