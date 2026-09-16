"""Single cloud worker with a persistent volume. Requires explicit Telegram setup.

Drives live AL candidates from the V2 hourly-Donchian-20/40/80 model re-run on
4h bars -- see ARASTIRMA.md for why V1 (config.json) was dropped from the live
path (0/5 walk-forward windows) in favour of V2 (2/5 windows passed, the best
result found; still not a full GO, deployed as a small bounded real-money
experiment at the user's explicit request, not as a proven strategy).
"""
import json
import os
import time
from pathlib import Path
from .data import download
from .paper_trading import tick
from .research_v2 import DonchianModel, FOUR_HOUR
from .risk import validate_config
from .telegram import deliver_once, deliver_paper_events

SEED_DAYS = 30  # >80 four-hour bars (Donchian's longest lookback) before any signal can fire.


def main():
    if not os.environ.get('TELEGRAM_BOT_TOKEN') or not os.environ.get('TELEGRAM_CHAT_ID', '').isdecimal():
        raise SystemExit('Set TELEGRAM_BOT_TOKEN and verified TELEGRAM_CHAT_ID in hosting secrets')
    root = Path(os.environ.get('CRYPTO_STORAGE', '/var/data'))
    root.mkdir(parents=True, exist_ok=True)
    config = validate_config(json.loads(Path('config_v2.json').read_text(encoding='utf-8')))
    database = root/'telegram.sqlite'
    deliver_once('connection:'+os.environ['TELEGRAM_CHAT_ID'],
                 'Kripto V2 (4 saatlik Donchian) Telegram baglantisi kuruldu. '
                 'Veriler hazirlaniyor. Gercek emir yalnizca iki bagimsiz kilit '
                 'acikken ve AL onayiyla verilir.', database)
    if not (root/'data-v2-live/manifest.json').exists():
        download(config, root/'data-v2-live', SEED_DAYS, interval=FOUR_HOUR)
    while True:
        tick(config, root/'data-v2-live', root/'paper-v2/state.json', root/'reports-v2',
             model=DonchianModel, interval=FOUR_HOUR)
        deliver_paper_events(root/'paper-v2/state.json', database)
        print('V2 update completed', flush=True)
        time.sleep(300)  # 4h bars close infrequently; no need to poll every 30s.


if __name__ == '__main__':
    main()
