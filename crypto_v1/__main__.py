import argparse
import json
import time
import sys
from pathlib import Path
from .data import download, load, INTERVAL
from .risk import validate_config
from .backtest import run, report
from .paper_trading import tick
from .research_v2 import evaluate


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='Crypto V1: public data + simulated trades only')
    parser.add_argument('--config', default='config.json')
    sub = parser.add_subparsers(dest='command', required=True)
    fetch = sub.add_parser('fetch')
    fetch.add_argument('--days', type=int, default=90)
    fetch.add_argument('--data', default='data')
    backtest = sub.add_parser('backtest')
    backtest.add_argument('--data', default='data')
    backtest.add_argument('--output', default='reports/backtest')
    backtest.add_argument('--holdout', type=float, default=0.3)
    paper = sub.add_parser('paper')
    paper.add_argument('--data', default='data')
    paper.add_argument('--state', default='paper/state.json')
    paper.add_argument('--output', default='reports/paper')
    paper.add_argument('--watch', action='store_true')
    research = sub.add_parser('research-v2')
    research.add_argument('--data', default='data-v2')
    research.add_argument('--output', default='reports/research-v2')
    args = parser.parse_args()
    c = validate_config(json.loads(Path(args.config).read_text(encoding='utf-8')))
    if args.command == 'fetch':
        if args.days < 4:
            parser.error('At least 4 days required')
        download(c, args.data, args.days)
    elif args.command == 'backtest':
        if not 0 < args.holdout < 1:
            parser.error('holdout must be between 0 and 1')
        manifest, data = load(args.data)
        times = [r['t'] for r in data['BTCUSDT'] if manifest['start'] <= r['t'] < manifest['end']]
        if len(times) < 400:
            parser.error('At least 400 BTC candles required')
        split = times[200 + int((len(times)-200)*(1-args.holdout))]
        for name, start, end in [('full', times[200], manifest['end']), ('development', times[200], split), ('holdout', split, manifest['end'])]:
            state = run(data, manifest['symbols'], c, start, end)
            meta = dict(mode='historical_backtest', segment=name, start=start,
                        end=end or times[-1]+INTERVAL, universe=manifest,
                        note='Fixed rules; holdout not optimized. Current-universe bias remains.')
            result = report(state, c, meta, Path(args.output)/name)
            print(name, json.dumps(result['metrics']))
    elif args.command == 'research-v2':
        manifest, data = load(args.data)
        result = evaluate(data, manifest['symbols'], c, args.output)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        stop_path = Path(args.state).parent/'stop.request'
        while True:
            if args.watch and stop_path.exists():
                stop_path.unlink()
                print('Paper stopped gracefully.', flush=True)
                break
            print(json.dumps(tick(c, args.data, args.state, args.output)['metrics']), flush=True)
            if not args.watch:
                break
            time.sleep(30)


if __name__ == '__main__':
    main()
