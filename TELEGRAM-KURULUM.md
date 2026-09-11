# Telefon üzerinden Telegram takibi

Bot kullanıcı adı: @Kripto1907_bot. Kullanıcı botta Başlat'a bastığını bildirdi. Bot tokenı ve doğrulanmış özel sohbet ID'si henüz bağlanmadı; sunucu henüz oluşturulmadı, test mesajı gönderilmedi.

Telegram gönderim kodu ve sunucu paketi hazırlandı. Yerel paper komutu mesaj göndermeye devam etmez; gönderim yalnızca ayrıca yapılandırılan cloud çalışanıyla açılır. Backtest mesajları gönderilmez.

Sunucuda ayarlanacak gizli değerler:

- TELEGRAM_BOT_TOKEN: BotFather anahtarı. Sohbete, kaynak koda veya depoya yazılmamalı.
- TELEGRAM_CHAT_ID: kullanıcının doğrulanmış özel sohbet numarası. Bot kullanıcı adı bunun yerine geçmez; rastgele ilk mesaj gönderen kişiye otomatik bağlanılmaz.
- CRYPTO_STORAGE: kalıcı disk yolu; varsayılan /var/data.

Sunucu tek çalışan olarak `python -m crypto_v1.cloud` çalıştırır. Dockerfile hazırdır. İlk başlatmada 14 günlük gösterge verisi alınır ve yeni paper oturumu başlar. Mevcut yerel paper geçmişi otomatik aktarılmaz. Aktarım istenirse yerel süreç güvenli durdurulup data ve paper klasörleri doğrulanarak kalıcı diske taşınmalıdır.

Kalıcı disk zorunludur: paper durumu, fiyat geçmişi ve Telegram teslim kayıtları burada tutulur. Gönderim öncesinde kayıt ayrılır; belirsiz ağ hatasında otomatik yeniden gönderim yapılmaz. Bu, çift mesaj riskini azaltır fakat belirsiz teslimlerin operatör tarafından incelenmesini gerektirir. Teslim durumu telegram.sqlite içindedir. Her mesaj sanal işlem olarak işaretlenir. Veri veya bağlantı hatası çalışmayı durdurur; sunucu logu kontrol edilmelidir. Ani süreç ölümünde eski paper kilidi manuel inceleme gerektirebilir. Gözetimsiz işletim ve canlı teslim henüz doğrulanmadı.

Render olası barındırma seçeneğidir; arka plan çalışanı ve kalıcı disk için hizmet planı/maliyeti hesap üzerinden doğrulanmadan ücretli kaynak oluşturulmaz. Bu paket henüz Render'a yüklenmedi veya dağıtılmadı.

## Ücretsiz GitHub Actions seçeneği

Kullanıcının ücret ödememe tercihi üzerine GitHub Actions çalışma dosyası eklendi. Herkese açık depoda standart GitHub çalıştırıcısı ücretsizdir. Bot anahtarı yalnızca `TELEGRAM_BOT_TOKEN` adlı GitHub Actions secret alanına girilir. Kod, daha önce bota gönderilmiş tek özel `/start` mesajından sohbet numarasını bulur; birden fazla özel `/start` varsa güvenli biçimde durur. Paper durumu `runtime-state` dalında tek commit olarak tutulur. Anahtar, rapor veya fiyat verisi bu dala yazılmaz.

Zamanlanmış işler tam dakikada çalışmayı garanti etmez; GitHub yoğunluğunda gecikme olabilir. Bu yöntem 15 dakikalık mum kapanışı sonrasında sinyal bildirir ve gerçek emir vermez.

Resmî belgeler:
- https://core.telegram.org/bots/api#sendmessage
- https://render.com/docs/background-workers
- https://render.com/docs/disks
- https://render.com/docs/configure-environment-variables
