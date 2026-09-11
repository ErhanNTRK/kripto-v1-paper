"""Single cloud worker with a persistent volume. Requires explicit Telegram setup."""
import json
import os
import time
from pathlib import Path
from .data import download
from .paper_trading import tick
from .risk import validate_config
from .telegram import deliver_once, deliver_paper_events


def main():
    if not os.environ.get('TELEGRAM_BOT_TOKEN') or not os.environ.get('TELEGRAM_CHAT_ID', '').isdecimal():
        raise SystemExit('Set TELEGRAM_BOT_TOKEN and verified TELEGRAM_CHAT_ID in hosting secrets')
    root = Path(os.environ.get('CRYPTO_STORAGE', '/var/data'))
    root.mkdir(parents=True, exist_ok=True)
    config = validate_config(json.loads(Path('config.json').read_text(encoding='utf-8')))
    database = root/'telegram.sqlite'
    deliver_once('connection:'+os.environ['TELEGRAM_CHAT_ID'],
                 'Kripto V1 Telegram baglantisi kuruldu. Sanal takip icin veriler hazirlaniyor. Gercek emir verilmiyor.', database)
    if not (root/'data/manifest.json').exists():
        download(config, root/'data', 14)
    while True:
        tick(config, root/'data', root/'paper/state.json', root/'reports')
        deliver_paper_events(root/'paper/state.json', database)
        print('Paper update completed', flush=True)
        time.sleep(30)


if __name__ == '__main__':
    main()
