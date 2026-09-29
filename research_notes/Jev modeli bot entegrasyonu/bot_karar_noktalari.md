# Bulutların Efendisi: where a fast "decision-only" AI model could plug in (decision points and data flows, as of 29 Sep 2026)

Scope and conventions
- Code read: live checkout `C:/Users/ASUS-PC/kripto/src` at commit **b07a944** (confirmed by read-only `GET /health` → `"commit": "b07a944fcfa6"`). The dev repo `C:/Users/ASUS-PC/Documents/GitHub/kripto-v1-paper` is at the same commit (only `deploy/windows-baslat.ps1` deleted locally), so the line numbers below hold for both.
- Path abbreviations: `RW` = `crypto_v1/render_web.py`, `GW` = `crypto_v1/github_worker.py`, `LSC` = `crypto_v1/live_short_controller.py`, `LL` = `crypto_v1/live_limits.py`, `LS` = `crypto_v1/live_signal.py`, `R5` = `crypto_v1/research_v5.py`, `LM` = `crypto_v1/live_monitor.py`, `LSM` = `crypto_v1/live_short_monitor.py`, `LSMK` = `crypto_v1/live_short_market.py`, `LE` = `crypto_v1/live_execution.py`, `TP` = `crypto_v1/telegram_poll.py`, `BT` = `crypto_v1/backtest.py`, `SS` = `crypto_v1/short_signal.py`, `SH` = `crypto_v1/shadow.py`. All live under `C:/Users/ASUS-PC/kripto/src/`.
- Memory notes are marked "(memory)". They are point-in-time notes from earlier sessions. Where one conflicted with the code, I followed the code and say so.
- Nothing was modified. The only live reads were `GET /health` and `GET /status`; the code says `/status` touches Binance "not at all" (RW:1234-1253). No Binance keys were used.

## 1. Architecture: main loop, 4H/2H sleeves, signal generation, universe, capital split, risk, stops, skipped-signal replay

### Takeaway
One Python process (`render_web.main`) runs both sleeves in a single 120-second loop thread. Each iteration does three things in order: exits for both apps, then detection (once per new 4H/2H candle), then entries for both apps. Every entry decision is plain rule code that flows through a short chain of gates. There is no model anywhere and no hook for one. The candidate hand-off between detection and execution is a JSON file in `runtime/`. That file is the cleanest seam for an AI layer.

### Cited Findings
**Process and loop**
- `main()` builds two `LiveApp` instances:
  - tag "4": 4H, `config_v5_long.json` + `short_live_config.json`, state files `state-relaxed.json` / `short_state.json`, guard `daily_guard_4.json`.
  - tag "2": 2H, `config_v5_long_2h.json` + `short_live_config_2h.json`, `state_2h.json` / `short_state_2h.json`, `daily_guard_2.json`.
  - Both share `runtime/manifest.json`. Sources: [RW:1593-1661](C:/Users/ASUS-PC/kripto/src/crypto_v1/render_web.py).
- `main()` also starts:
  - the watchdog thread (RW:1671);
  - the Telegram polling thread `TelegramApprovals` (RW:1672-1676);
  - the loop thread `run_periodic_scans(APPS, detect=local_tick(runtime_dir), housekeeping=refresh_ledger)` (RW:1689-1691);
  - an HTTP server on 127.0.0.1:10000 (RW:1695-1696).
- `LOOP_SECONDS = 120` (RW:1310). `run_periodic_scans` runs `tick_all(entries=False)` (exits), then `detect()`, then `tick_all(exits=False)` (entries), then housekeeping, then sleeps 120 s ([RW:1313-1373](C:/Users/ASUS-PC/kripto/src/crypto_v1/render_web.py)).
- `tick()` takes one lock shared by both apps (`_SHARED_TICK_LOCK`, RW:232) without blocking, and returns "busy" when the lock is held (RW:1167-1200).
- `_tick`:
  - skips everything during the Binance rate-limit cooldown (RW:1202-1204);
  - runs `daily_guard()` then `scan()` for exits (RW:1207-1213);
  - runs `auto_enter()` for entries (RW:1215-1221).
- Rate-limit breaker: 300 s base cooldown, doubling on each hit, capped at 3600 s, shared by both apps (RW:73-114).

**Detection (signal generation)**
- `GW.local_tick` runs detection only when a new 4H or 2H window has started (`_DETECTED_WINDOWS`), using `now = serverTime // FOUR_HOUR * FOUR_HOUR` ([GW:299-366](C:/Users/ASUS-PC/kripto/src/crypto_v1/github_worker.py)).
- 4H longs come from the paper `backtest.Engine` via `paper_trading.tick(... model=ShortWindowLongModel)` (GW:349-359).
  - The Engine emits `AL_ADAYI` events at `time=t+interval` with `pending_buys[symbol]={stop, score, breaks_up}`.
  - It only does so for symbols the paper engine does not already hold: `if symbol not in s['positions']` ([BT:142-169](C:/Users/ASUS-PC/kripto/src/crypto_v1/backtest.py)).
  - The audit flagged this "3-slot paper Engine" coupling (memory, fable_audit_2026_09_25.md).
- 4H shorts come from `write_short_state` (GW:218-245). 2H longs and shorts come from `write_2h_signal_state` (GW:248-296). Both call `SS._detect`, which evaluates only the last closed bar of each symbol ([SS:15-49](C:/Users/ASUS-PC/kripto/src/crypto_v1/short_signal.py)).
- Entry rule chain in `R5.long_entry` ([R5:198-293](C:/Users/ASUS-PC/kripto/src/crypto_v1/research_v5.py)):
  1. Donchian breakout (`breaks_up >= min_breaks`; `entry_mode` is "breakout" on both systems since 111c1de).
  2. BTC filter `btc_up_ok` ("loose" = BTC close > EMA50, R5:29-52).
  3. BTC above its 200-day SMA (`regime_ma_days` 200, R5:236-238).
  4. `max_week_gain` 0.5, 4H only (R5:245-248).
  5. Ichimoku "fully bullish" on the coin's own timeframe, 9/26/52: close above cloud, Tenkan > Kijun, cloud ahead green, Chikou above ([R5:67-92](C:/Users/ASUS-PC/kripto/src/crypto_v1/research_v5.py), applied at R5:251-252).
  6. `max_extension_atr` 1.0 (R5:258-262).
  7. `skip_first_time_high` (R5:266-271).
  8. Stop = close − 2×ATR (`atr_multiplier` 2.0), with a cost hurdle (R5:290-293).
- Donchian windows: 4H uses the default (10, 20, 40) (R5:25). 2H uses [20, 40, 80] (`config_v5_long_2h.json`). Features are built in `symmetric_features` (R5:95-167).
- Shorts: `short_entry` (R5:296-312). 2H has `disable_shorts: true`. 4H has `short_regime_only: true`, so shorts fire only while BTC is below its 200-day average.
- Trend exit: `long_exit` fires when BTC fails its filter for `btc_exit_bars` (2 on 2H), or when close drops below the 20-bar (4H) / 40-bar (2H) low (R5:371-390).

**Universe**
- `prepare_data` re-ranks at most once per 24 h using `weekly_universe` (7-day volume) and writes `runtime/manifest.json` (GW:99-162).
- Current manifest: ranked_by "7d_volume", 50 symbols including BTC, ranked_at 1790596800000 (28 Sep 2026, per file mtime) (`runtime/manifest.json`).
- `_auto_symbols` treats the first `auto_entry_top_n` = 30 as automatic; everything else needs Telegram approval. An unreadable list asks about every coin (fail closed) ([RW:498-509](C:/Users/ASUS-PC/kripto/src/crypto_v1/render_web.py)).

**Capital split and sizing**
- 4H: `pilot_capital_fraction` 0.35, `ethbtc_filter_ema_days` 50, `max_open_positions` 6. 2H: 0.65, no gate, `max_open_positions` 10 (`short_live_config.json`, `short_live_config_2h.json`).
- Equity per sleeve = account equity × fraction ([LSMK:240-275](C:/Users/ASUS-PC/kripto/src/crypto_v1/live_short_market.py)).
- Risk per trade = equity × `risk_per_trade_fraction` 0.01 × `quality_risk_multipliers` [1.5, 0.5] (breakout vs momentum) ([LL:6-32](C:/Users/ASUS-PC/kripto/src/crypto_v1/live_limits.py)).
- Round-up to Binance's minimum is allowed up to `max_risk_per_trade_fraction` 0.01 (LL:35-42; [LE:84-150](C:/Users/ASUS-PC/kripto/src/crypto_v1/live_execution.py)).

**Stops and exits**
- Protective STOP_MARKET is placed right after the fill (LSC:228-239).
- The resting stop trails at `trailing_atr × resting_stop_trail_multiple` (2) ATR behind the best price once the trade is 1R ahead ([RW:900-910, 986-1041](C:/Users/ASUS-PC/kripto/src/crypto_v1/render_web.py)).
- The real trailing exit is close-based: `LM.exit_decision` sells when close ≤ high − `trailing_atr`×ATR. `trailing_atr` = 6 (4H) or 8 (2H), from the strategy config, which overrides the `trailing_atr: 2.0` left over in `short_live_config*.json` (RW:879-883; [LM:8-33](C:/Users/ASUS-PC/kripto/src/crypto_v1/live_monitor.py)).
- Exits close first, then cancel the stop ([LSM:57-86](C:/Users/ASUS-PC/kripto/src/crypto_v1/live_short_monitor.py)).

**Daily and account limits**
- Daily 10% stop: `daily_guard` snapshots equity per UTC day, checks once per bar, and on −10% closes everything and blocks entries until the UTC day ends ([RW:1075-1165](C:/Users/ASUS-PC/kripto/src/crypto_v1/render_web.py)). Today's state: `daily_guard_4.json` equity 67.87, `daily_guard_2.json` 158.37, both `halted:false`.
- Other limits, all in `may_open` ([LL:60-83](C:/Users/ASUS-PC/kripto/src/crypto_v1/live_limits.py)):
  - pilot loss limit: 40% of baseline (baseline = net deposits × fraction);
  - `daily_loss_limit_fraction` 0.1;
  - `max_buys_per_day` 40;
  - `already_holding_symbol`.

**Skipped-signal replay**
- `_record` sends capital rejections to `shadow.record` ([RW:593-625](C:/Users/ASUS-PC/kripto/src/crypto_v1/render_web.py)). The capital reasons are "risk target below Binance minimum", "order is below Binance minimums" and "plan exceeds available margin" ([SH:21-25, 45-60](C:/Users/ASUS-PC/kripto/src/crypto_v1/shadow.py)).
- `shadow.replay` plays each one forward with the live exit rules (SH:63-96). The 09:00 TR summary reports the result (RW:1488-1495).

### Inferences
- The file `runtime/state*.json` (`events` + `pending_buys`) is effectively a message bus between detection and execution. An AI filter could annotate or veto candidates there without touching order code. The cost is that `_write_candidate_state` merges by symbol within a window (GW:165-199), so a vetoed symbol could come back on the next write. A separate "vetoes" store is cleaner.
- All live decisions run in one thread. Any synchronous AI call inside `auto_enter` delays the next exit pass for both sleeves by the call's duration.

### Gaps
- I did not read `paper_trading.tick` or `backtest.Engine`'s sizing in full. How often the 3-slot paper Engine suppresses a real 4H candidate was not measured.
- `data.weekly_universe` (data.py:135) was only listed by name, not read. The exact filters (listing age, futures-tradable) are asserted in comments only.

## 2. Telegram "AL" approval flow

### Takeaway
Human approval exists only for coins ranked 31–50 in the daily 7-day-volume list. Ranks 1–30 are bought automatically with no human step. The approval is an in-memory flag set by a Telegram long-poll thread. It expires with the signal, 30 minutes after the bar close. An AI approver could set the same flag through the existing `LiveApp.approve(symbol)` call, exactly as the Telegram poller does. That makes it the lowest-risk integration point.

### Cited Findings
- **Gate.** In `auto_enter`, a long candidate whose symbol is not in the top-30 set goes through `_cleared()`. If it is not cleared, `_ask_approval()` runs and the result is recorded as `awaiting_approval` ([RW:422-426, 511-517](C:/Users/ASUS-PC/kripto/src/crypto_v1/render_web.py)).
- **Question.** `_ask_approval` ([RW:524-562](C:/Users/ASUS-PC/kripto/src/crypto_v1/render_web.py)):
  - asks only if the bot could take the trade right now (it runs `pilot_status()` + `may_open`);
  - stores `{created_at, expires_ms, side, approved: False}` in `self._approvals`;
  - `expires_ms = created_at + signal_confirmation_expiry_minutes × 60000`, which is 30 min in both configs;
  - sends one question per candidate.
- **What the question shows** (RW:553-556): side, symbol, volume rank, the automatic cutoff (30), signal close, stop price and stop distance %, risk in USDT, minutes left, and the reply syntax `AL <COIN>`. It shows no chart, indicators, BTC/ETH-BTC state, funding, or open-position context.
- **Reply path.**
  - `TelegramApprovals.run` long-polls `getUpdates` with `POLL_SECONDS = 25` ([TP:31-118](C:/Users/ASUS-PC/kripto/src/crypto_v1/telegram_poll.py)).
  - `handle` accepts `AL <COIN>`, or bare `AL` when exactly one coin is waiting, and calls `app.approve(symbol, now_ms)` on every app (TP:69-94).
  - `approve` sets `approved=True` if the request has not expired (RW:564-573).
  - The next 120-s tick buys the coin through `_approve_long`.
- **Re-checks after approval.** Everything is checked again: the ETH/BTC gate (RW:316), `confirmed_signal` with the expiry (LL:86-96), `may_open` (LSC:177-183), and a price drift of at most `max_entry_drift_fraction` 0.01 (1%) above the signal close ([LSC:184-189](C:/Users/ASUS-PC/kripto/src/crypto_v1/live_short_controller.py)).
- **Persistence.** Approvals live in memory only (`self._approvals`, RW:271), so they are lost on restart. The audit flagged "approvals in memory only" (memory, fable_audit_2026_09_25.md). A backlog of messages sent while the bot was down is skipped at startup (TP:63-67).
- **Render-only path.** `LiveApp.telegram()` (RW:687-736), behind `do_POST /telegram` (RW:1541-1550), takes every pending signal on a bare "AL". It is not used on the PC, because the webhook was deleted and polling is used instead (memory, local_bot_environment.md).
- **Research on ranks 31–50.** Trade level, ranks 31–50 had PF 1.18 (2023-26) and 0.44 (2021-23) against 1.42 / 1.26 for ranks 1–30. The portfolio multi-start result was mixed, so ranks 31–50 were kept behind approval (memory, research_findings_2026_09_23.md, "Rank 31-50 check").
- **Hand exits.** The user's hand exits beat the bot by +29.5 USDT in the week of 22–28 Sep, mostly SAGA. The same discretionary exits written as rules were all worse over 3 years (memory, research_findings_2026_09_23.md).

### Inferences
- An "AI approver" thread modelled on `TelegramApprovals` could:
  - read `app.awaiting_approval()` (RW:575-578);
  - build context for each waiting symbol;
  - call `app.approve(symbol)` or do nothing.
- No change to order code is needed. Default-deny on timeout keeps today's behaviour (unapproved requests expire). Ranks 31–50 are also the weakest bucket, where a veto costs the least tail.
- Using AI to veto top-30 automatic entries is a different and riskier change. The research shows every trade-level filter tried so far lowered the portfolio total (see Q4).

### Gaps
- No log or history of how many approval questions were sent or answered. They are not persisted and not in `bot.log`, so the base rate of human approvals is unknown.

## 3. Does the bot already call any LLM/AI API? Keys, timeouts, fallbacks

### Takeaway
No. The live bot has no LLM or ML dependency, no AI API key, and no AI config. Its only outbound calls go to Binance (public and signed) and Telegram, all through stdlib `urllib` with fixed timeouts. AI has so far been used only offline, in research and review.

### Cited Findings
- `requirements.txt` contains only `cryptography==45.0.7` (`C:/Users/ASUS-PC/kripto/src/requirements.txt`).
- A grep for `openai|anthropic|claude|gpt|llm|gemini|ollama` across `*.py/*.json/*.yml/*.ps1` finds no API use. The only hit is a comment about the "user's Gemini notes" ([R5:223-225](C:/Users/ASUS-PC/kripto/src/crypto_v1/research_v5.py)).
- The secrets folder holds only `BINANCE_API_KEY`, `BINANCE_ED25519_PRIVATE_KEY`, `LIVE_TRADING_CONFIRMATION`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` and `TELEGRAM_WEBHOOK_SECRET` (`C:/Users/ASUS-PC/kripto/secrets`, filenames only).
- Existing timeout patterns:
  - public Binance `get()`: 30 s, 5 retries with backoff ([data.py:35-64](C:/Users/ASUS-PC/kripto/src/crypto_v1/data.py));
  - signed futures calls: 20 s (binance_futures.py:77);
  - Telegram `send_message`: 20 s, raising `RuntimeError` on failure ([telegram.py:18-34](C:/Users/ASUS-PC/kripto/src/crypto_v1/telegram.py));
  - runtime-state fetch: 20 s ([LS:21-24](C:/Users/ASUS-PC/kripto/src/crypto_v1/live_signal.py)).
- **Fail-closed conventions** the code already uses:
  - ETH/BTC gate unreadable → longs blocked (RW:478-484);
  - manifest unreadable → ask about every coin (RW:498-503);
  - unknown regime or Ichimoku → no entry (R5:236-252).
- **Per-candidate exception isolation.** `_attempt` catches `ValueError` as a rejection and any other exception as "failed" with a Telegram alert, then moves to the next candidate ([RW:652-685](C:/Users/ASUS-PC/kripto/src/crypto_v1/render_web.py)).
- **Prior AI use, all offline:**
  - scikit-learn ML ranking and meta-labeling in the research venv `~/kripto/arastirma-venv`;
  - the user's ChatGPT reviewed the verification package on 28 Sep;
  - "TradingView Technical Rating" reimplementation. (All memory, research_findings_2026_09_23.md.)

### Inferences
- A new AI client would be the first third-party network dependency in the decision path. It should follow the existing patterns:
  - a hard timeout well under the 120 s loop;
  - an exception mapped to a documented default, fail-closed for entries;
  - its own circuit breaker, like `_note_if_rate_limited`;
  - a new secret file next to the others.
- Adding an SDK would also be the first non-stdlib runtime dependency besides `cryptography`.

### Gaps
- Whether the PC's outbound network (office link with modem failover, per memory) is reliable enough for a per-candle API call was not measured.

## 4. Known weaknesses an AI decision layer could or could not address

### Takeaway
The audit and research record a trend-following system whose profit is a thin tail: the median trade is −0.3R, and the top 2% of trades make 70–79% of R. It bleeds in regimes where BTC is above its 200-day line but altcoins chop. Survivorship inflated the research numbers heavily. Every trade-level filter tried so far, including ML meta-labeling, lowered the portfolio result. The only regime gate that held up on both datasets was ETH/BTC above its EMA50, which is now live on 4H. So an AI veto layer faces a strong prior against it. Its most plausible uses are:
- regime/breadth risk-gating (reducing drawdown in bad eras);
- event/news risk (information the bot has none of today);
- monitoring and operations;
- screening the approval-only ranks 31–50.

Picking winners among top-30 breakouts is the least plausible use.

### Cited Findings
**Tail dependence**
- Profit is a tail: median trade −0.3R, top 2% of trades = 70–79% of R (memory, fable_audit_2026_09_25.md).
- 95 of 1984 trades (5R+) made 56% of gains (memory, research_findings, 28 Sep entry).

**ML and filters that failed**
- ML ranking with 41 features, labelled +2R before −1R: all deciles negative, and the top 1% was the worst.
- Meta-labeling 21,684 rule signals was non-monotonic (quintile avg R +0.16/+0.06/+0.02/+0.21/+0.26). Gating the portfolio made it worse: 408 → 252 (skip lowest 30%) and 265 (skip lowest 50%).
- Conclusion recorded: "Price/volume ML adds nothing over the rules." (All memory, research_findings.)
- Autopsy: the weak groups (high ATR%, >40% above the 10-day low, momentum entries, low signal volume) were all still slightly positive, so filtering them lowered the total from 1766 to 1500–1630 (memory, research_findings).
- Trade-level signals that did not survive the portfolio test: ranks 31–50, and the distance-to-30-day-top cap (memory, research_findings).

**New data that looked promising**
- Taker buy/sell ratio over the 4h before entry was the most consistent: PF 0.98 → 2.01 in discovery and 0.56 → 1.71 in validation, with 7 of 16 5R+ winners in the top quintile.
- Funding, OI change and top-trader long/short showed no consistent effect. The taker candidate ("skip if taker4h < ~1.02") still needs a full portfolio PIT test. (Memory, research_findings, "OI/funding/positioning at entry".)

**Regime weakness**
- Blind 2021–2023 PIT test at the live rules: 0.35x, DD 96%. 2023H1 lost −84% while BTC rose +84%, with correlated stop clusters (26 Apr 2023: 10 positions stopped in one bar).
- Every breadth or alt-index filter overfit to one era. Only ETH/BTC > EMA50 worked on both datasets. Multi-start windows showed that slow ETH/BTC filters cut bad-era tails without hurting good-era medians, and the gate was deployed on 4H in b07a944 (memory, research_findings).
- Every variant lost in Sep 2025–Feb 2026 (−25…−41%): "a market-regime problem no filter fixes" (memory, research_findings, PIT structural fixes).

**Inflation, noise and path sensitivity**
- Sim inflated about 30–40%: today's top-50 applied to 3 years, the 360-day listing age not applied point-in-time, and the sim letting 4H and 2H hold the same coin. A live-like replay gives ~4.3–4.8 R/week, not 7 (memory, full_review_2026_09_24.md).
- PIT vs fixed list: 4.6x vs 108x under the old rules (memory, research_findings).
- Ordering noise: shuffling same-bar candidates gives 1700–3303 final equity (memory, full_review).
- Path sensitivity: vol-target "gains" were mostly path noise, so ideas should be judged with multi-start or resampled runs (memory, research_findings).

**Whipsaws and false signals**
- The live churn "bought 11:00, sold 13:00" led to `btc_exit_bars` 2 on 2H (memory; code at R5:386-390).
- NIL/SAGA "first-time vertical move" losses led to `skip_first_time_high` and `max_extension_atr` (R5:253-271).

**News and event risk**
- No code path reads news, listings/delistings, unlock schedules, funding or OI at decision time. The only inputs are klines, account and orders (`SS._detect` uses klines only, SS:15-49; `analysis` uses klines only, [LSMK:631-653](C:/Users/ASUS-PC/kripto/src/crypto_v1/live_short_market.py)).

**Operational defects an AI monitor could watch** (from the audit; memory, fable_audit_2026_09_25.md)
- pilot limit measured against frozen baselines (A1);
- daily guard mis-measures hand closes (A2);
- a breached stop left unprotected (A3);
- adoption of hand-opened positions (A4);
- no auto-start after reboot;
- double-run not prevented.

Several of these were later fixed (3f6a719: A1–A8 + C2; the bekci watchdog). The code shows the A2 and A3 fixes (RW:1126-1145, RW:859-875).

### Inferences
- Where an AI layer could plausibly add information:
  - (a) news/event/listing/delisting risk, which is not in the rules at all;
  - (b) a regime or breadth read. The history is sobering: 10 of 11 regime candidates overfit;
  - (c) human-readable context for ranks 31–50 approvals;
  - (d) anomaly monitoring, such as correlated-stop clusters, stale stops, or a stalled loop.
- Where it probably cannot help, given the evidence: second-guessing top-30 breakouts on price and volume alone. Meta-labeling already showed that gating lowered the total. With 56% of gains in 5% of trades, any veto with a modest false-negative rate on the tail will cost more than it saves.
- An LLM evaluated on 2021–2025 history is contaminated by its own training knowledge of what happened to those coins. The research's standard, a point-in-time universe with discover-on-one and validate-on-another, cannot be met for a pretrained LLM on any period before its knowledge cutoff. Only a forward (live/paper) test is clean. This is my inference, not tested.

### Gaps
- No study so far of news or event features. Whether event days explain the correlated stop clusters (e.g. 26 Apr 2023) is unknown.
- The taker-ratio filter's portfolio-level PIT test is still pending (memory).

## 5. Latency budget per decision, and where a slow or failed AI call is dangerous vs harmless

### Takeaway
Candles close every 2H/4H, but the real entry budget is short:
- detection finishes about 10 s to 2 min after the close;
- the first entry attempt follows seconds later (AVAX today: filled 140 s after the bar close);
- the price must stay within 1% of the signal close;
- the candidate expires 30 min after the close.

A pre-trade AI call of a few seconds is affordable. Anything over roughly 60 s moves the entry away from the tested "next-bar open" assumption. Anything that blocks the single loop thread for more than 15 minutes kills the process through the watchdog. AI must never sit in the stop placement, ratchet, protect, exit or daily-guard paths.

### Cited Findings
**Measured timings**
- `bot.log` detection lines on 29 Sep: "4H long tarama" at 11:00:27, "2H tarama" at 11:00:36 and 13:02:14 (1 AL_ADAYI). Detection ends about 0.5–2 min after the TR bar close (`C:/Users/ASUS-PC/kripto/src/runtime/bot.log` lines 571-578).
- `GET /status` → `recent_entries["2"]`: AVAXUSDT `signal_time` 1790676000000, `opened_and_protected` at 1790676140.4, i.e. **140 s after the bar close**. The next 12 attempts (every ~129 s) were rejected as `already_holding_symbol`.
- Slippage audit over 39 live entries: fills ~139 s after the close, median −7 bp against the 1m open at the signal close (memory, research_findings).

**Hard limits in code and config**
- `signal_confirmation_expiry_minutes` = 30 in both `short_live_config*.json`. The comment in `auto_enter` (RW:390-399) says 10, which is stale; the config value (30) is what applies.
- `max_entry_drift_fraction` = 0.01: the entry is rejected if the price is more than 1% above the signal close (LSC:187-189).
- `LOOP_SECONDS` = 120 (RW:1310). `ENTRIES_PER_TICK` = 3 per side per app (RW:228).
- `WATCHDOG_SECONDS` = 15×60. If `LOOP_PROGRESS` is not updated for 15 min, the watchdog dumps threads and calls `os._exit(3)` ([RW:1381-1424](C:/Users/ASUS-PC/kripto/src/crypto_v1/render_web.py)). A 2H detection pass "can take ~7 min on a slow link" (RW:1381 comment).
- `pilot_status` cache is 90 s (LSMK:297-299).
- Telegram approval poll is 25 s (TP:32). Approval still needs the next 120-s tick.

**Dangerous places for a slow or failed call** (order/stop-critical)
- `_approve_long` → `approve_long_leveraged`, from the margin/leverage calls through stop placement and emergency close (LSC:210-239). The time between fill and stop is unprotected.
- `_protect`: re-places missing stops (RW:912-980).
- `_ratchet`: places the new stop, then cancels the old one (RW:986-1041).
- `scan` / `_manage_futures_position`: exits (RW:738-898).
- `_close_then_cancel`: close-first, then cancel (LSM:57-86).
- `daily_guard` / `_close_all`: the −10% day liquidation (RW:1075-1165).
- The shared tick lock (RW:1190-1191) and the single loop thread (RW:1342-1373). A slow call in entries delays the next exits for both sleeves. The code notes that exits "are the one thing that must never silently stop running" (RW:739-747).

**Harmless or optional places** (the bot already fails safe or keeps running without them)
- Candidate pre-screen before `_attempt` in `auto_enter` (RW:414-436), which rejects a candidate before any order.
- The approval decision for ranks 31–50 (`_ask_approval`/`approve`).
- Ranking (`LS._strongest_first`, LS:45-46), which matters only when capacity binds.
- The daily ETH/BTC gate (once per UTC day, cached, fail closed; RW:466-494).
- `daily_summary` (RW:1457-1498), the shadow report, and ledger housekeeping (RW:1680-1688).
- Monitoring: `_watch_scan_failures` (RW:821-842) and bekci.py.

**Existing precedent for external status**
- The resting exchange stop protects positions while the process is down: the code says "the exchange-native protective stop ... does not depend on this loop" (RW:1177-1180, 1320-1322).
- The resting stop is deliberately wide (2× trail), so while the loop is stalled the close-based trail is not running (RW:900-907).

### Inferences
- Practical budget for an entry-side AI call: at most ~5–15 s per candidate, synchronous. There can be up to 3 per tick per app, plus 31–50 approval questions.
- Better: run the AI asynchronously (its own thread or process, triggered when the state file changes), write verdicts to a file, and have `auto_enter` only read them. A missing verdict falls back to today's rules (fail-open) or to "skip" (fail-closed). Which default applies is a trading decision for the user. Fail-closed on top-30 would turn every API outage into missed trend trades, which the tail-dependence data warns against.
- A slow AI call in `auto_enter` cannot break a stop that is already resting. It does, however:
  - delay the fill, raising drift rejections;
  - delay the next exit and ratchet pass for all open positions.

### Gaps
- There is no per-phase timing in the logs (exit pass vs detection vs entries), so the current slack inside the 120-s loop is unknown. Only detection completion times are logged.

## 6. Data available at each decision point to feed a model

### Takeaway
At each decision point the bot holds only a thin slice of what it computes. The published candidate carries symbol, close, stop, breakout count, a volume-ratio score and a timestamp. The rich feature row (EMAs, ATR, RSI, Ichimoku state, week gain, BTC row) is computed in detection and then thrown away. At the approval step the bot adds account state and the live price. Funding, OI, taker ratio, news and order book are not fetched live at all, though research caches exist for funding and OI.

### Cited Findings
**Detection (full feature row, in memory only)**
- `symmetric_features` (R5:95-167) plus `indicators.features` (`crypto_v1/indicators.py:26-50`) give each row:
  - o/h/l/c/v/t;
  - atr, ema20/50/200, rsi, volume_avg, resistance/support;
  - breaks_up/down, long_exit/short_exit, regime_ma;
  - ichimoku_ok, break_level, range_high, week_gain, btc_weak_run.
- The BTC row carries the same fields. Detection uses about 45 days of 4H/2H klines per symbol (GW:154-157, 272-277).

**Published candidate** (`runtime/state_2h.json` today)
- `{"events":[{"type":"AL_ADAYI","time":1790676000000,"symbol":"AVAXUSDT","close":11.55}], "pending_buys":{"AVAXUSDT":{"stop":10.9013…,"leverage":4,"breaks_up":3,"score":2.9588…}}, "window":…}`.
- Fields are defined at GW:279-287 (2H) and BT:155-169 (4H). `pending_candidates` exposes symbol, created_at, close, stop, leverage? and breaks_up? (LS:49-75).

**Approval step**
- `pilot_status()` returns ([LSMK:275-285](C:/Users/ASUS-PC/kripto/src/crypto_v1/live_short_market.py)):
  - open_positions, held_symbols, opens_today;
  - realized_loss_today, pilot_drawdown, pilot_baseline;
  - free_usdt, equity;
  - realized_pnl_today, own_unrealized, own_unrealized_by_symbol;
  - opened/closed_symbols_today.
- It also has the live price `market.price`, contract `rules`, and the plan from `leveraged_order_plan`: quantity, stop_price, planned_loss_usdt, margin_usdt, leverage (LE:143-150).
- ETH/BTC ratio and EMA come from `ethbtc_trend_ok` (RW:192-211).

**Exit step**
- `analysis(position, ...)` returns the latest coin row, the BTC row and the extreme since entry, from about 220 bars of klines (LSMK:631-653).
- The position dict holds entry, stop_price, quantity, stop_client_id, open_time and side.

**History and logs**
- `runtime/bot_orders.json`: the bot's own filled orders beyond Binance's 7-day window.
- `runtime/shadow_trades.json`: capital-skipped signals.
- `~/kripto/islem-kayitlari/{islemler.csv, ozet.txt, binance-kayitlari.json}`: the trade ledger, refreshed hourly (RW:1677-1688).
- `runtime/bot.log`: rotating at 5 MB (RW:1553-1590).
- `/status` `recent_entries`: the last 30 entry attempts with reasons (RW:282, 1234-1253).

**Research-only data** (not live) under `C:/Users/ASUS-PC/kripto/arastirma-verisi/`
- `data_4h.json`, `data_2h.json`, `funding.json` (3.5 years, 50 coins);
- `oi_funding/` (data.binance.vision daily metrics zips: 5-min OI, top/global long-short, taker ratio);
- `pit/` (daily_top50.json, spot1d/2h/4h, symbols.json) and `pit2020/`;
- `prepump_4h.json`.
- Sources: directory listing; memory, local_bot_environment.md and research_findings.

### Inferences
- A model fed only the published candidate would see less than the rules already use. To give it the feature row, `SS._detect` / `BT.step` would need to persist the row (or a chosen subset) in `pending_buys`, which is cheap. The alternative is to re-fetch klines, which spends Binance weight (the 23 Sep research-induced slowdown is a caution; memory).
- Live funding, OI and taker-ratio features would need new futures public calls. `data.futures_get` exists (data.py:67), but no live code path uses it for features.

### Gaps
- Whether Binance's live OI and taker-ratio endpoints could be called per candidate within the rate budget was not checked.

## 7. Backtest/walk-forward harness for evaluating an AI filter offline, and the changes it needs

### Takeaway
The repo's own harnesses are older than the current rules and lack PIT universes:
- `research_v5.run_symmetric` / `evaluate`;
- `walkforward.py`;
- `backtest.Engine`.

The trustworthy harness is a set of scratchpad scripts, not committed to the repo:
- `pit_sim.py` (point-in-time top-50, 4H+2H joint, live rules, realistic costs);
- `pit_multistart.py` (12-month windows starting every month);
- `ml_meta.py` (walk-forward meta-labeling).

An AI filter fits in cleanly at the per-bar candidate list. The evaluation must use PIT universes, multi-start windows, both eras (2021–23 blind and 2023–26), realistic costs, and strict closed-bar inputs. For a pretrained LLM it also has to deal with training-data contamination.

### Cited Findings
**Repo harnesses**
- `run_symmetric` (R5:397-523) and `evaluate` (R5:526-560). `walkforward.evaluate` (`crypto_v1/walkforward.py:35-63`). `backtest.run` (BT:178-193).
- Memory: `run_symmetric` had a lookahead bug (it re-checked entry on the entry bar's close) until 7e9ca54, and "Don't cite pre-fix run_symmetric numbers" (memory, research_findings).
- Audit: "the 'last year' was looked at for every variant, so no holdout remains". The only walk-forward of live-shaped rules was NO_GO 2/6 (memory, fable_audit).

**PIT harness** (`C:/Users/ASUS-PC/AppData/Local/Temp/claude/C--Users-ASUS-PC-Documents-GitHub-kripto-v1-paper/6c302beb-1425-4631-b8af-97cbc9ea449d/scratchpad/pit_sim.py`, 196 lines)
- Precomputes per-bar entry lists `ent[k][t].append((symbol, stop, score, breaks_up))` (lines 64-67).
- Queues them at the end of bar t as `pend_buy[sn] = [...]` (line 140).
- Fills them at the next bar's open, ranked by score (lines 104-123).
- Applies the daily PIT universe (`allowed = daily.get(...)`, lines 103, 108), the "no coin in both sleeves" rule (line 108), and the joint −10% day halt (lines 150-154).
- Sequences 4H before 2H (lines 147-149), matching live.
- Companion scripts in the same folder: `pit_multistart.py` (67 lines), `ml_meta.py` (159 lines), `pool_sim.py`.
- PIT data reads `PIT_DIR` (pit vs pit2020) (memory).

**Other lookahead and noise traps recorded**
- Building daily closes from 4H bars with key `(t+4h)//DAY` leaks up to 20 h; the correct rule is to keep only bars with `(t+4h) % DAY == 0` (memory, "LOOKAHEAD GOTCHA").
- Pyramiding results varied 18x vs 30x with `PYTHONHASHSEED` because `pend_add` was a set (memory).
- Ordering noise of 1700–3303 final equity from same-bar shuffles (memory, full_review).
- The live ETH/BTC gate deliberately ignores the still-open daily candle (`int(r[6]) < now_ms`, RW:202-203).

**Evaluation standard the user has accepted** (memory, research_findings)
- multi-start 12-month windows, reporting median / worst / worst-DD per era;
- discover on one dataset, validate on the other;
- a portfolio-level test (trade-level stats misled twice);
- realistic costs: fee 0.05% per side + 2 bp slippage;
- funding still unmodelled.

**Scorecard**: any AI method should also be scored on the fixed 100-point rubric (Getiri 25, Risk 20, Sağlamlık 20, Canlı doğrulama 15, Operasyonel güvenlik 10, Uygulanabilirlik 10). The prior score for "ML ranking/meta-labeling" was ~10 (memory, system_score.md).

### Inferences
**Minimal harness change for an AI filter.** Add a hook `ai_filter(sn, t, candidates, context) -> kept` where `pend_buy` is built (pit_sim.py line 140). `context` should hold only data with timestamps ≤ the signal bar's close (t + interval):
- the feature rows up to bar t;
- BTC and ETH/BTC daily closes whose close time is < t + interval;
- funding/OI/taker metrics stamped ≤ t + interval.

Also cache every verdict to disk, keyed by (symbol, t), so runs are repeatable and cheap.

**Contamination.** For an LLM, any backtest period before its knowledge cutoff is contaminated: it may "know" that a coin later pumped or delisted. Mitigations, each a partial fix:
- anonymise symbols and dates and normalise prices;
- give only numeric features;
- rely mainly on a forward paper test.

The live `shadow.py` replay (SH:63-96) is already a forward-test pattern that could be reused. Record AI-vetoed candidates as shadow trades and replay them with the live exit rules, so vetoes can be scored against the trades actually taken.

**Promotion bar.** Given ordering noise, any AI filter must beat the baseline across seeds and windows, not on one path. It must also be checked for the tail: how many 5R+ winners it vetoed.

### Gaps
- The PIT harness is not in version control; it lives in a session scratchpad. It has to be copied into the repo (e.g. `research/`) before anyone relies on it.
- I did not read `pit_multistart.py` or `ml_meta.py` line by line. Their hook points are inferred from memory notes and `pit_sim.py`'s structure.
- Funding cost is unmodelled in the long sims (memory, fable_audit), so an AI filter that shifts holding times would be judged with a known bias.

---

### Appendix (within the Q7 notes): decision-point index for the report writer

| # | Decision point | File:line | Current logic | AI role that fits | Failure default | Criticality |
|---|---|---|---|---|---|---|
| U1 | Daily universe rank | GW:99-162; data.py:135 | top-50 by 7d volume, futures-tradable | ranker/monitor (flag pump-driven entrants) | keep yesterday's list (already) | low (once/day) |
| U2 | Auto vs approval split | RW:498-517 | top-30 auto, 31-50 approval | approver for 31-50 | no approval (expires) | low |
| D1 | Signal detection | GW:309-366; SS:15-49; BT:142-169; R5:198-293 | Donchian + BTC loose + 200d + Ichimoku + ext/first-high/week-gain | none (keep rules); optionally annotate/persist features | n/a | n/a |
| G1 | ETH/BTC gate (4H only) | RW:192-211, 466-494, 413-421, 316 | ETH/BTC > EMA50 daily, fail closed | regime monitor/second opinion | blocked (already) | medium |
| G2 | Daily equity stop | RW:1075-1124, 384-385 | -10% UTC day → close all, halt | monitor only | rules | HIGH, no AI |
| R1 | Candidate ranking | LS:35-46, 75; RW:414-416 | (breaks, volume score), 3/tick | ranker | rules' order | low-medium |
| F1 | Pre-trade veto (top-30) | RW:414-436 (before `_attempt`) | none | filter/risk gate | fail-open = today's behaviour | medium (tail risk) |
| A1 | Human approval | RW:524-573; TP:69-94 | Telegram "AL <COIN>" within 30 min | auto-approver / context writer | not approved | low |
| L1 | Limits | LL:60-83; LSC:176-183 | position, daily, pilot, held symbol | none | rules | HIGH, no AI |
| S1 | Sizing | LL:6-42; LE:84-150; LSC:190-200 | 1% × quality mult, min-notional round-up ≤1% | risk multiplier (e.g. 0.5x on event risk) | 1.0x | medium |
| O1 | Order + stop placement | LSC:204-241 | market open, liquidation check, STOP_MARKET | none | n/a | CRITICAL, no AI |
| X1 | Exit decision | RW:844-898; LM:8-33; R5:371-390 | trend/trailing (6/8 ATR close-based), BTC exit | advisory only (hand exits as rules all lost) | rules | HIGH |
| X2 | Protect/ratchet | RW:912-1041 | re-protect, 2× trail resting stop | none | n/a | CRITICAL, no AI |
| M1 | Monitoring and summary | RW:821-842, 1384-1498; bekci.py; SH:118-146 | Telegram alerts, 09:00 summary, shadow | monitor/explainer (anomalies, stop clusters) | skip | low |
