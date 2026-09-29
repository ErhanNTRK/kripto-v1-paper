# Evidence on LLM decision layers in systematic crypto/futures trading bots (as of 2026-09-29)

Context: an Ichimoku trend-following bot on Binance USDT-M futures, 4H and 2H timeframes, top-30 coins by volume, ATR trailing stops, and human approval via Telegram. The question is whether to add a fast, non-reasoning "decision-only" LLM, and where it would go.

Evidence grading used below:
- **[MEASURED]**: peer-reviewed or arXiv study with a stated protocol.
- **[LIVE-ANECDOTE]**: real-money competition, small sample, no significance testing.
- **[SECONDARY]**: blog or aggregator. Treat with caution.

## 1. What 2024–2026 studies and benchmarks show about LLMs as trading agents or filters (incl. reasoning vs non-reasoning)

### Takeaway
No rigorous study shows that an LLM decision layer reliably beats simple baselines after costs and over long horizons. Positive results come from short windows (about 3–4 months), narrow universes, or backtests exposed to lookahead. The only real-money evidence, Alpha Arena, shows most frontier models losing money when they trade autonomously on crypto perps. Reasoning ("thinking") models do not consistently beat fast instruct models at trading.

### Cited Findings
**Long-horizon, bias-controlled evaluation [MEASURED]**
- FINSABER (KDD 2026) re-ran LLM timing strategies such as FinMem and FinAgent over about 20 years and 100+ symbols. It found that "previously reported LLM advantages deteriorate significantly under broader cross-section and over a longer-term evaluation." — [arXiv 2505.07078](https://arxiv.org/abs/2505.07078); [GitHub FINSABER](https://github.com/waylonli/FINSABER)
- FINSABER regime finding: LLM strategies were "overly conservative" in bull markets and underperformed buy-and-hold. They were "overly aggressive in bear markets, incurring heavy losses." The authors call for "trend detection and regime-aware risk controls" rather than more framework complexity. — [arXiv 2505.07078](https://arxiv.org/abs/2505.07078)

**Survey audit of reproducibility [MEASURED]**
- "Agentic Trading" survey (May 2026): of 77 papers, only 19 both output actions and use closed-loop evaluation. Among those 19:
  - only 2 report extractable time-consistent splits;
  - only 1 has an explicit transaction-cost model;
  - only 1 handles universe or survivorship bias;
  - 15/19 sit at the lowest reproducibility tier (R0), and none reach R3.
  - The survey does not extract any live-trading performance claim with full protocol. — [arXiv 2605.19337](https://arxiv.org/html/2605.19337v1)

**Crypto-specific agent [MEASURED, 2024]**
- CryptoTrade (EMNLP 2024) is a reflective LLM agent that uses on-chain and off-chain news data for daily trading. It beat time-series baselines but was "not [superior] compared to traditional trading signals" such as Buy-and-Hold and MACD. Performance was only "comparable". — [ACL Anthology](https://aclanthology.org/2024.emnlp-main.63/); [GitHub](https://github.com/Xtra-Computing/CryptoTrade)

**Multi-agent stock system [MEASURED, 2024, weak protocol]**
- TradingAgents (Dec 2024):
  - Window: 1 Jan – 29 Mar 2024, 5 mega-cap tech stocks (AAPL, NVDA, MSFT, META, GOOGL).
  - Reported cumulative return 23.2–26.6% and Sharpe 5.60–8.21, against baselines of −5.2% to 17.1% (Sharpe 0.17–3.53).
  - Model split: "quick-thinking" gpt-4o-mini/gpt-4o for data tasks, and "deep-thinking" o1-preview for decisions.
  - The authors say the window was limited to 3 months because each prediction needed "11 LLM calls & 20+ tool calls". — [arXiv 2412.20138](https://arxiv.org/html/2412.20138)

**Contamination-free stock benchmark, reasoning vs instruct [MEASURED]**
- StockBench: 3 Mar – 30 Jun 2025 (82 trading days). The buy-and-hold baseline returned +0.4% with −15.2% max drawdown.
  - Reasoning models: Qwen3-235B-Think +2.5%, Qwen3-30B-Think +2.1%, o3 +1.9%.
  - Non-reasoning models: Qwen3-235B-Instruct +2.4% (best max drawdown, −11.2%), GLM-4.5 +2.3%, Claude-4-Sonnet +2.2%, Kimi-K2 +1.9% (best Sortino, 0.042 vs 0.0155).
  - Conclusion: reasoning did not uniformly beat instruct, and "excelling at static financial knowledge tasks does not necessarily translate into successful trading." — [StockBench](https://stockbench.github.io/)
- LiveTradeBench (Nov 2025) streams live prices and news. It found that "high LMArena scores do not imply superior trading outcomes". Models show distinct risk styles. — [arXiv 2511.03628](https://arxiv.org/html/2511.03628v1)

**Live real-money competitions [LIVE-ANECDOTE]**
- Alpha Arena Season 1 (nof1.ai), 18 Oct – 3 Nov 2025: each model had $10k on Hyperliquid crypto perps. Final balances:

  | Model | Final balance |
  |---|---|
  | Qwen3-Max | $12,231 |
  | DeepSeek | $10,489 |
  | Claude Sonnet 4.5 | $5,799 |
  | Gemini 2.5 Pro | $5,445 |
  | Grok | $4,208 |
  | GPT-5 | $4,126 |

  — [ForkLog](https://forklog.com/en/four-out-of-six-ai-models-suffer-losses-in-trading-tournament/); [iWeaver (secondary)](https://www.iweaver.ai/blog/alpha-arena-ai-trading-season-1-results/)
  - A secondary source attributes the large losses to overleverage and weak risk controls. This is not verified from nof1. — [iWeaver](https://www.iweaver.ai/blog/alpha-arena-ai-trader-showdown/)
- Alpha Arena Season 1.5 (US equity tokens, ended 3 Dec 2025): 8 models × 4 modes = 32 accounts, $320k in total.
  - The four modes were baseline+news, "Monk" capital preservation, situational awareness, and max leverage.
  - Only 6 of 32 accounts ended positive. The aggregate result was −$112.6k (−35.2%).
  - The Season 1 winner, Qwen3-Max, lost in all four modes. A "mystery model" won with about +12.1% aggregate. — [GitHub research PR citing nof1 leaderboard](https://github.com/josejuarez96/tradepartner/pull/233); [OneDayAdvisor](https://www.onedayadvisor.com/2025/12/nof1ai-alpha-arena-review-season-15.html); [PANews](https://www.panewslab.com/en/articles/b195204d-bf4c-4bb4-8beb-a1e858166b08)
- nof1's own methodology post warns: "statistical power is limited and early standings can move… run-to-run variation in both rankings and inter-model correlations". No significance test is reported. nof1 has since removed the Season 1 per-model pages. — [GitHub PR #233](https://github.com/josejuarez96/tradepartner/pull/233); [nof1.ai](https://nof1.ai/)

**Crypto papers reporting large gains [MEASURED but unverified protocol]**
- A multi-modal LLM crypto system reports +133.5% cumulative return and Sharpe 1.50 on the top-15 L1 coins. — [arXiv 2604.26747 / search summary](https://arxiv.org/html/2604.26747v1)
- A sentiment-aware crypto paper reports SMA-crossover Sharpe rising from 0.34 to 3.47 after adding LLM sentiment. — [arXiv 2508.16378](https://arxiv.org/pdf/2508.16378)
- Neither number was checked against the full text, and both are the kind of result the survey audit flags as rarely cost- or split-controlled.

### Inferences
- The best-controlled studies (FINSABER, StockBench, the survey audit) point the same way: gains shrink toward zero as the protocol gets stricter. An LLM overlay should be treated as unproven until it shows a gain in the user's own forward shadow test.
- The FINSABER failure is too conservative in uptrends and too aggressive in downtrends. That is exactly backwards for a trend-follower. An LLM veto layer on Ichimoku entries could cut winners in strong trends, and trend-following profit depends on a few large winners.
- Fast instruct models look as good as reasoning models for trading decisions (StockBench), so latency and cost favor a fast model. That does not show that either kind adds value over the rules.
- Alpha Arena shows that the damage in autonomous LLM trading comes mostly from sizing, leverage and exits, which are the parts this bot already handles with rules.

### Gaps
- No study found that tests an LLM veto on top of Ichimoku or other trend-following signals on crypto perps with walk-forward evaluation after the model's cutoff.
- Season 1 per-model trade counts, fees, leverage and decision interval could not be verified from nof1 primary pages, which have been removed.
- No Alpha Arena seasons after 1.5 (2026) were found in this search.
- FinMem, StockAgent and FinBen were not fetched individually. FinMem is covered only indirectly through FINSABER.

## 2. Which integration patterns work (filter/veto, regime, news gate, sizing, ops monitoring, auto-approval, full autonomy)

### Takeaway
The evidence is weakest for full autonomy and strongest (by design, not by P&L proof) for narrow, advisory roles: event/news risk flags, ops and log triage, and summarizing for the human approver. LLM-derived features can be statistically valid and still hurt the trading policy under regime shift.

### Cited Findings
- **(g) Full autonomy:** Alpha Arena Season 1.5 had 26 of 32 accounts losing, −35.2% in aggregate, and Season 1 had 4 of 6 losing. FINSABER finds no durable edge over long horizons. — [PR #233](https://github.com/josejuarez96/tradepartner/pull/233); [arXiv 2505.07078](https://arxiv.org/abs/2505.07078)
- **(a/c) LLM features as filters/inputs:** "When Valid Signals Fail" (Apr 2026) fed frozen-LLM features from news and filings into a PPO agent. Prompts were optimized against Information Coefficient.
  - The features reached IC > 0.15 on held-out data.
  - But "during a distribution shift caused by a macroeconomic shock, LLM-derived features add noise, and the augmented agent under-performs a price-only baseline". Performance recovered in calm periods.
  - Macro state variables were the most robust inputs. — [arXiv 2604.10996](https://arxiv.org/abs/2604.10996)
  - Note: a first automated summary of this PDF invented regime percentages ("8–12%"). The abstract does not contain them, so they are excluded here.
- **(b) Regime classification:** FINSABER explicitly recommends "trend detection and regime-aware risk controls" as what LLM strategies lack. That points to regime logic being done by rules or statistical models rather than delegated to the LLM. — [arXiv 2505.07078](https://arxiv.org/abs/2505.07078)
- **(c) News/sentiment:** CryptoTrade's news-plus-on-chain agent only matched Buy-and-Hold and MACD. Sentiment papers reporting large Sharpe gains have unaudited protocols (see section 1). — [ACL 2024](https://aclanthology.org/2024.emnlp-main.63/); [arXiv 2508.16378](https://arxiv.org/pdf/2508.16378)
- **Architecture guidance:** the survey reframes agents as perception, memory, reasoning, action, risk control and logs. It places risk control as "rule constraints on inference-to-action path", with typed order contracts and immutable logs. — [arXiv 2605.19337](https://arxiv.org/html/2605.19337v1)
- **(d) Sizing:** Alpha Arena's worst losses were attributed (secondary source) to overleverage and poor risk control by the models. — [iWeaver](https://www.iweaver.ai/blog/alpha-arena-ai-trader-showdown/)

### Inferences
Patterns ranked for this bot, from lowest to highest risk:
1. **(e) Ops monitoring and log triage.** No P&L exposure; it catches bot defects, which have historically been this project's main losses. Recommended first.
2. **Telegram approval assistant.** The LLM writes a short structured "context card" per signal (news, funding, open-interest spikes, BTC regime, correlated exposure) for the human, who still decides. Low risk and testable.
3. **(c) Event-risk gate.** The only filter allowed is a veto for hard, verifiable events such as a delisting, exploit, or unlock/listing news in the last N hours. It must be logged and measured in shadow first.
4. **(a) General discretionary veto on trend entries.** Risky: FINSABER and the regime-shift paper suggest it will remove bull-trend winners.
5. **(d) Sizing** and **(f) auto-approval**: not recommended. There is no supporting evidence, and they remove the human safeguard.
6. **(g) Full autonomy:** evidence is negative.

Keep regime detection (b) in rules, for example the existing ETH/BTC gate or a BTC EMA filter.

### Gaps
- No controlled study was found comparing "LLM veto" and "no veto" on an otherwise identical rule-based crypto strategy.
- No study was found measuring LLM usefulness for ops/log triage in trading bots specifically. The case for it is engineering logic, not measured evidence.

## 3. Failure modes and how to backtest an LLM filter honestly

### Takeaway
Two problems dominate: parametric lookahead (the model has memorized outcomes from its training period) and non-determinism that persists even at temperature 0. Only evaluation after the model's knowledge cutoff, ideally forward and live-shadow, is trustworthy. Every LLM output must be cached so the evaluation can be replayed.

### Cited Findings
**Lookahead**
- "Detecting Lookahead Bias in LLM Forecasts" (Dec 2025) measures a Lookahead Propensity (LAP) score: whether the model can recall the outcome from only the firm, ticker and date.
  - In-sample, a one-standard-deviation increase in LAP amplifies the LLM signal's effect by about 32% for news and about 12% for earnings calls (t = 3.64 and 2.01).
  - Out-of-sample (2024) the effect becomes insignificant (t = 1.06).
  - LAP "collapses essentially to zero" right after Llama-3.3-70B's Dec 2023 cutoff. — [arXiv 2512.23847](https://arxiv.org/html/2512.23847v2)
- "Parametric look-ahead bias" lives in the model's weights and is invisible to data-pipeline audits. — [arXiv 2605.24564](https://arxiv.org/html/2605.24564)
- DeepFund evaluates each model only on dates after its own training cutoff. — [arXiv 2605.24564](https://arxiv.org/html/2605.24564)
- Mitigation papers exist, such as inference-time unlearning with small helper models. — [arXiv 2512.06607](https://arxiv.org/html/2512.06607v1)

**Other data leakage**
- News timestamps often record publication time rather than when the item actually became available to the pipeline.
- Memory or reflection modules can leak outcomes. The survey's fix is an "Outcome Embargo": an episode's outcome cannot be retrieved until t+k.
- Hallucinated tool outputs propagate through agent loops.
- Long contexts degrade decisions ("lost in the middle").
- Only 1 of 19 primary studies models transaction costs. — [arXiv 2605.19337](https://arxiv.org/html/2605.19337v1)

**Non-determinism**
- At temperature 0, measured non-determinism persisted across 2 providers, 3 model tiers and 5 sampling configurations, including greedy top_k=1. In 690 calls, 1–2 of 7 borderline items were not reproducible. — [arXiv 2606.26185](https://arxiv.org/html/2606.26185)
- The Determinism-Faithfulness harness ran 4,700+ runs (7 models, 4 providers, 3 finance benchmarks) at T=0 and found no detectable correlation between decision determinism and task accuracy. — [alphaXiv 2601.15322](https://www.alphaxiv.org/abs/2601.15322)
- Root cause: floating-point and batching effects flip near-tied tokens. — [Thinking Machines Lab](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/)

**Model instability and cost**
- Alpha Arena rankings vary from run to run, and the Season 1 winner lost in all Season 1.5 modes. — [PR #233](https://github.com/josejuarez96/tradepartner/pull/233)
- Evaluation cost limits testing: TradingAgents' 11 LLM calls plus 20+ tool calls per decision confined its backtest to 3 months. — [arXiv 2412.20138](https://arxiv.org/html/2412.20138)

### Inferences
Honest backtest recipe for this bot:
1. Use only bars after the model's published knowledge cutoff. In practice this means forward shadow mode from deployment day, because any new model's cutoff is recent.
2. Freeze the prompt and model version, set temperature 0, and cache every request and response keyed by (symbol, bar close time, prompt hash) so evaluation replays the logged outputs instead of re-calling the model.
3. Build inputs strictly from data timestamped before the bar close. Use news "first seen" time, not publication time.
4. Compare veto-applied vs no-veto P&L on the same signals, including fees and funding, and measure the veto's hit rate. Most of a trend strategy's P&L sits in the right tail, so check that the tail winners were not vetoed.
5. Given about 30 symbols and a few signals per week, expect months of shadow data before any confidence. Consider pre-registering the decision rule and a minimum trade count.
6. Test sensitivity: re-run a sample of cached inputs multiple times and with paraphrased prompts, and log the flip rate.

### Gaps
- No crypto-perps-specific measurement of LLM veto flip rates was found.
- No published minimum sample size exists for validating an LLM filter on a low-frequency trend strategy. It would have to be estimated from the bot's own trade frequency and P&L variance.

## 4. Practical engineering best practices (structured outputs, fallbacks, no stop control, shadow mode, logging, cost)

### Takeaway
Treat the LLM as an untrusted, optional advisor behind a typed contract. The deterministic rule path must always work without it, and the model must never touch order placement, stops, or sizing.

### Cited Findings
- The survey recommends:
  - a typed action interface ("typed order contracts");
  - a rule-based risk-control layer between inference and action;
  - immutable logs with provenance;
  - reporting faithfulness, leakage checks and compute budget for any reasoning step. — [arXiv 2605.19337](https://arxiv.org/html/2605.19337v1)
- Temperature 0 is "necessary but not sufficient". Replayability requires logging and caching outputs. — [arXiv 2606.26185](https://arxiv.org/html/2606.26185); [DFAH harness](https://www.alphaxiv.org/abs/2601.15322)
- Correct views still lose money through position management. The Season 1.5 lesson is that the agent "must turn an analysis into a position, choose size and leverage, manage the position, and decide when to exit". — [OneDayAdvisor/PANews summary](https://www.onedayadvisor.com/2025/12/nof1ai-alpha-arena-review-season-15.html)

### Inferences
Engineering checklist:
- **Output contract:** return JSON only, for example `{"verdict": "allow" | "veto" | "flag", "reason_code": "<enum>", "confidence": 0–1, "evidence_ids": [...]}`. Validate it against a schema. Any parse failure, timeout (for example over 10–20 s) or API error resolves to a deterministic default. For a veto-type layer the default is "allow", meaning the rules plus the human decide as today. Use a retry budget of 1, and add a circuit breaker after N consecutive failures.
- **Hard boundaries:**
  - The LLM never places or cancels orders, moves stops, or changes size or leverage. ATR trailing stops and the daily 10% stop stay purely rule-based.
  - The LLM can only reduce exposure, by vetoing or flagging, and never add it.
  - Human Telegram approval stays in place.
- **Rollout order:**
  1. Shadow mode: log the verdict and do not act on it.
  2. Show the card in Telegram as advice.
  3. Only after a pre-registered evaluation, possibly enable automatic veto of hard events.
- **Logging:** record the prompt hash, model ID and version, input snapshot, raw output, latency, token counts, verdict, and the realized forward outcome of the signal (for example R-multiple at exit) for later scoring.
- **Cost:** under 1 USD/day at plausible fast-model prices. Estimate, assuming prices:

  | Scenario | Calls per day | Tokens per day | Cost per day |
  |---|---|---|---|
  | Call every bar for all 30 symbols on both timeframes (worst case) | 30 × (12 two-hour bars + 6 four-hour bars) = 540 | about 1.1M input + 0.1M output (at 2k input, 200 output per call) | about $1–2 at an assumed ~$1/M input and ~$5/M output |
  | Call only when the rules produce a signal (typically a few per day) | a few | small | cents |

  - Prices must be checked against the chosen provider's current price list.
  - Prompt caching of the static system prompt lowers cost further.
  - Latency matters little on 2H and 4H bars, but the timeout must stay well inside the bar-to-order window.

### Gaps
- No published cost benchmark specific to per-bar crypto LLM filters was found. The figures above are an arithmetic estimate on assumed prices and token counts, not sourced data.
- No source was found on outage frequency of LLM APIs during volatile market periods.

## 5. Regulatory/risk considerations for an individual in Turkey (brief)

### Takeaway
Law No. 7518 (in force 2 July 2024) put crypto asset service providers operating in Turkey under the Capital Markets Board (SPK). It regulates platforms, not individuals' use of bots or AI. The practical issues for an individual are tax declaration of foreign-exchange gains and growing automatic reporting (CARF), not a ban on AI or algorithmic trading.

### Cited Findings
- Law 7518, amending the Capital Markets Law, entered into force on 2 July 2024 and placed crypto asset service providers operating in Turkey under SPK regulation and supervision. — [SPK announcement](https://spk.gov.tr/duyurular/basin-duyurulari/2024/kripto-varlik-hizmet-saglayicilara-iliskin-duyuru_02072024)
- Global exchanges such as Binance are not SPK-licensed and remain usable by Turkish investors, but the legal picture is changing. — [Sanayi Gazetesi / search summary](https://sanayigazetesi.com.tr/yeni-kripto-yasasi/)
- A tax bill was submitted to parliament in March 2026. Its proposed terms:
  - 10% final withholding on gains made through SPK-licensed platforms;
  - a 0.03% transaction tax;
  - gains on unlicensed or foreign exchanges declared annually at progressive income-tax rates of 15–40%.
  - As of that source's March 2026 date it was a **draft, not enacted**. The current status was not verified. — [Vergi Merkezi](https://vergimerkezi.com.tr/turkiye-kripto-vergi-rehberi-2026-stopaj-fifo-beyan/)
- Turkey is moving to OECD-CARF, under which foreign exchanges would share Turkish users' transaction histories and balances with the Ministry of Treasury and Finance. — [Vergi Merkezi](https://vergimerkezi.com.tr/turkiye-kripto-vergi-rehberi-2026-stopaj-fifo-beyan/)
- The tax guidance found does not specifically address futures/derivatives or algorithmic/AI trading by individuals. — [Vergi Merkezi](https://vergimerkezi.com.tr/turkiye-kripto-vergi-rehberi-2026-stopaj-fifo-beyan/)

### Inferences
- Using an LLM does not by itself change the legal position. The user remains fully responsible for every order, and human approval via Telegram strengthens that accountability.
- Keep complete trade logs, including LLM verdicts, for tax declaration and dispute purposes.
- Sending account data or positions to a third-party LLM API is a data-exposure and privacy consideration. Never send API keys, and minimize account identifiers.

### Gaps
- Whether the March 2026 crypto tax bill has been enacted as of 29 Sep 2026 was not verified.
- The tax treatment of perpetual-futures P&L and funding payments for individuals was not found.
- No Turkish rule specific to AI or automated trading by individuals was found. This is not legal advice; a Turkish tax adviser should confirm.
