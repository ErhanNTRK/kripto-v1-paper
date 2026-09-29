# Jev model identity: who made it, what it is, specs, access, reception (researched 2026-09-29)

## 1. Does "Jev" exist, who made it, when was it released?

### Takeaway
Yes, it exists. **Jev** is a proprietary model from **TypeSafe AI**, a San Francisco startup. It launched in limited early access in mid-September 2026. Most sources give **15 September 2026**, so the user's "last week" is roughly two weeks ago. TypeSafe calls it the first "System One model". Name confidence is high: many independent sources confirm it.

### Cited Findings
- Jev is a proprietary model from TypeSafe AI, a San Francisco company founded in 2024. It launched in limited early access on 15 Sep 2026 alongside a US$40M seed round led by DCVC. — [Wikipedia: Jev (AI model)](https://en.wikipedia.org/wiki/Jev_(AI_model))
- The seed round valued the company at US$200M. The current stable release is `jev-1.13.0` and the license is proprietary. — [Wikipedia](https://en.wikipedia.org/wiki/Jev_(AI_model))
- Founders are Diogo Almeida (CEO; about 4 years at OpenAI on RLHF, InstructGPT, ChatGPT and GPT-4), Erik Gafni and Sasha Sheng. — [Wikipedia](https://en.wikipedia.org/wiki/Jev_(AI_model)); [heise online, 17 Sep 2026](https://www.heise.de/en/news/AI-model-Jev-to-make-machines-decide-faster-11457071.html)
- Official announcement: "Introducing System One Models & Jev". — [TypeSafe AI Blog](https://typesafe.ai/blog/introducing-system-one-models-and-jev)
- TechCrunch covered it on 18 Sep 2026 as a release from "this week": "A new kind of AI model from a ChatGPT inventor is thrilling developers". — [TechCrunch](https://techcrunch.com/2026/09/18/a-new-kind-of-ai-model-from-a-chatgpt-inventor-is-thrilling-developers/)
- Bloomberg ran it on 25 Sep 2026: "Jev, an AI Model That Can't Chat, Takes On Bigger Rivals". Only the headline was seen. — [Bloomberg](https://www.bloomberg.com/news/articles/2026-09-25/jev-an-ai-model-that-can-t-chat-takes-on-bigger-rivals)
- The name comes from economist William Stanley Jevons and the "Jevons paradox": cheaper AI is expected to be deployed more. — [Wikipedia](https://en.wikipedia.org/wiki/Jev_(AI_model))
- The launch video was reportedly viewed about 40 million times on X. — [Wikipedia search snippet](https://en.wikipedia.org/wiki/Jev_(AI_model))

### Inferences
- The user's description ("only makes decisions instead of thinking") matches exactly how TypeSafe positions Jev as a "System One" model (fast, intuitive, Kahneman's System 1). No other candidate model needs to be considered.

### Gaps
- **Date conflict:** when WebFetch read TypeSafe's own blog page it reported "September 28, 2026". Wikipedia (15 Sep), heise (17 Sep), TechCrunch (18 Sep) and DataCamp (early access "as of September 15") all point to the middle of the month. The 28 Sep date may be a page-update date or a fetch/summary error; it could not be checked on the page itself. **The launch date is most likely 15 Sep 2026.**

## 2. What does "decides instead of thinks" mean technically?

### Takeaway
Jev is not an LLM. It does not generate text and has no chain-of-thought or "reasoning" setting. Input is text or program state plus schema-defined questions. Output is **typed values** (one of the allowed options, or a score) with **calibrated probabilities and confidence scores**. Generation is non-autoregressive: every answer is sampled in parallel in a single pass. It is trained with RL ("RLCD").

### Cited Findings
- The model is transformer-based, trained entirely on synthetic data with "Reinforcement Learning for Calibrated Decisions (RLCD)". It returns typed values and probability estimates, not natural-language text. — [Wikipedia](https://en.wikipedia.org/wiki/Jev_(AI_model)); [TechCrunch](https://techcrunch.com/2026/09/18/a-new-kind-of-ai-model-from-a-chatgpt-inventor-is-thrilling-developers/)
- There are three question primitives: **Choice** (pick from options), **Score** (rate on a range or scale) and **Noul** (yes/no probability). — [Wikipedia](https://en.wikipedia.org/wiki/Jev_(AI_model)); [DataCamp](https://www.datacamp.com/blog/system-one-models-jev)
- It uses "non-autoregressive parallel sampling" and "generates all outputs in a single query". It can return up to 255 options per decision. — [TypeSafe blog](https://typesafe.ai/blog/introducing-system-one-models-and-jev)
- It never produces an answer outside the schema, so there are no type errors or malformed objects. TypeSafe markets this as "can't hallucinate". — [TypeSafe blog](https://typesafe.ai/blog/introducing-system-one-models-and-jev); [Firecrawl blog](https://www.firecrawl.dev/blog/what-is-jev)
- Jev has no reasoning setting that can be turned on. — [Check Point Research](https://blog.checkpoint.com/ai-security/jev-is-not-a-language-model-but-it-breaks-like-one-prompt-injection-against-a-typed-decision-model/)

### Inferences
- In practice this is a "classifier/scorer as a service": you define the answer space in advance (for example AL/SAT/BEKLE, i.e. buy/sell/hold), and Jev returns a probability for each option. It is not a policy or action model trained on its own environment. It is a general-purpose decision head.

### Gaps
- Parameter count, architecture details and training data content are not disclosed (per [TypeSafe blog](https://typesafe.ai/blog/introducing-system-one-models-and-jev) and [Wikipedia](https://en.wikipedia.org/wiki/Jev_(AI_model))).

## 3. Capabilities, benchmarks, context, modalities, structured output

### Takeaway
Input is text only (string, JSON or an array of text), with a 64k-token context of which 32k is for state plus the longest question. Structured output is guaranteed. It is described as "tool calling" in the sense that a tool choice is just another choice question. Nearly all benchmarks are self-reported and use LLMs as the reference answer.

### Cited Findings
- Context is "64k tokens per request; 32k tokens for `state` plus the longest question". — [TypeSafe Docs: Models](https://docs.typesafe.ai/models)
- Input modality is text only: "String, JSON object, or array of text values. No image, audio, or video input." — [TypeSafe Docs](https://docs.typesafe.ai/models)
- English gives the best accuracy. Other languages (including CJK) are handled "but not equally well". **Turkish is not mentioned explicitly.** — [TypeSafe Docs](https://docs.typesafe.ai/models)
- The model aliases `jev-latest` and `jev-preview` both point to `jev-1.13.0`. The model is not fine-tuned on customer data. — [TypeSafe Docs](https://docs.typesafe.ai/models)
- Self-reported speed and cost: 40–200x faster than frontier LLMs and 40–400x cheaper. Internal workflows reach "193.6x faster, 444.6x cheaper". TypeSafe itself says these gains are "likely to sit at the high end of real-world results". — [Wikipedia](https://en.wikipedia.org/wiki/Jev_(AI_model)); [TypeSafe blog](https://typesafe.ai/blog/introducing-system-one-models-and-jev)
- On a 4-workflow benchmark, accuracy was 67.8% ("parity with mid-tier LLMs"), with a structured-output and tool-call error rate of 0%. — [DataCamp](https://www.datacamp.com/blog/system-one-models-jev)
- TypeSafe uses "the average of the answers from GPT-6 Astra and Claude Fable 5.1" as its accuracy reference, so the evaluation is circular and self-reported. — [heise online](https://www.heise.de/en/news/AI-model-Jev-to-make-machines-decide-faster-11457071.html)
- Customer reports: Vercel said Jev was 5–18x faster than OpenAI Luna 5.6 on safety classification and more accurate. Bryo AI said it was 10–20x cheaper than Gemini on email classification and "slightly less accurate". — [TechCrunch](https://techcrunch.com/2026/09/18/a-new-kind-of-ai-model-from-a-chatgpt-inventor-is-thrilling-developers/)
- Independent academic paper (arXiv 2609.27678, 23 Sep 2026, ContractNLI legal text): Jev had the lowest cost and median latency, but hosted LLMs had higher baseline accuracy. The paper also finds that "rankings by baseline accuracy differ from rankings by correctness", meaning decisions shift when the request configuration changes. — [arXiv: Same Scores, Different Decisions](https://arxiv.org/abs/2609.27678)
- Community classification example: BANKING77 at 92.40% accuracy, against 93.66% for a fine-tuned BERT. — [drillan finance survey gist, 20 Sep 2026](https://gist.github.com/drillan/6916b16e8ea31a8ec36c8f59d6483150)

### Inferences
- There is no independent public leaderboard result yet (LMArena / Artificial Analysis). Jev may not fit the chat benchmark format at all.

### Gaps
- No official benchmark table or independent latency/throughput measurement from Artificial Analysis or LMArena was found.

## 4. Access: API, SDK, open weights, license, price, latency, limits, Turkey/EU

### Takeaway
It is available only through a hosted API, in **early access with a waitlist**. It is closed-source with no open weights. Input costs **$0.042 per million tokens** and output is free. Latency is 70–500 ms. Limits are 250k tokens/s and 1,200 requests/min, and may change without notice.

### Cited Findings
- The endpoint is `POST https://api.typesafe.ai/v1/systemone` with model `jev-latest` and Bearer-token auth. Official Python and JavaScript SDKs exist. — [DataCamp](https://www.datacamp.com/blog/system-one-models-jev)
- Example request body: `{"model":"jev-latest","state":"...","questions":{"category":{"type":"choice","options":[...]},"urgency":{"type":"score","min":0,"max":100}}}`. — [DataCamp](https://www.datacamp.com/blog/system-one-models-jev)
- Pricing is $0.042 per million input tokens ($42 per billion). Output tokens are free. — [TypeSafe Docs](https://docs.typesafe.ai/models); [heise](https://www.heise.de/en/news/AI-model-Jev-to-make-machines-decide-faster-11457071.html)
- Latency is 70–500 ms end to end. — [TypeSafe blog](https://typesafe.ai/blog/introducing-system-one-models-and-jev); [heise](https://www.heise.de/en/news/AI-model-Jev-to-make-machines-decide-faster-11457071.html). Firecrawl reports a typical 100–200 ms. — [Firecrawl](https://www.firecrawl.dev/blog/what-is-jev)
- Rate limits are 250,000 tokens/s and 1,200 requests/min, and "can change without notice". Going over returns 429. Higher limits are offered on enterprise plans. — [TypeSafe Docs](https://docs.typesafe.ai/models); [search summary: OpenTweet/jevaiguide](https://opentweet.io/jev/limits)
- Demand was very high and the API briefly lost serving capacity. — [TechCrunch](https://techcrunch.com/2026/09/18/a-new-kind-of-ai-model-from-a-chatgpt-inventor-is-thrilling-developers/)
- The model is closed-source and proprietary, with no open weights. — [DataCamp](https://www.datacamp.com/blog/system-one-models-jev); [Wikipedia](https://en.wikipedia.org/wiki/Jev_(AI_model))

### Inferences
- At the price shown, one bot decision with about a 2k-token state costs about $0.00008, so cost is negligible. The real constraints are waitlist access and capacity/limit changes.

### Gaps
- **No regional restriction or explicit statement on Turkey/EU availability was found.** The docs say nothing about regions. The terms of service were not checked.
- The docs say nothing about SLA, uptime or data retention.

## 5. Finance/trading use, limitations, security, community reception

### Takeaway
The community quickly built dozens of crypto trading bots on Jev, but almost all run in paper/dry-run mode and **no verified profitability record exists**. The key caveats: it gives no rationale, it can be "confidently wrong", and prompt injection breaks it just as easily as an LLM. In finance-scenario tests, Check Point broke it in 59% of attempts.

### Cited Findings
- As of 20 Sep 2026 there were about 18–20 active finance/trading projects (on Monad/Kuru, Hyperliquid, Kraken and Binance). "Dry-run/paper-by-default is the norm". Only one project (aowang-ai/jev-trade) reports real fills on Hyperliquid, and its profitability is not disclosed. — [drillan finance survey gist](https://gist.github.com/drillan/6916b16e8ea31a8ec36c8f59d6483150)
- One example project is a crypto trading bot on Binance testnet that uses Jev to judge news. — [GitHub: Spykoninho/trading-bot-jev](https://github.com/Spykoninho/trading-bot-jev); another is a crypto signal classifier plus paper trader — [GitHub: devsoniclk/jev-crypto-decisions](https://github.com/devsoniclk/jev-crypto-decisions)
- The jev-trader project reports a decision latency of 81 ms and makes one decision per Monad block (~300 ms). — [search summary](https://gist.github.com/drillan/6916b16e8ea31a8ec36c8f59d6483150)
- Check Point tested an investment due-diligence scenario (24 Sep 2026). 59% of injection attempts succeeded, the strongest attacker reached 93% (25/27), the average compromise took 4.6 turns and cost about $0.50 per breach. Typed input made no meaningful difference, and adding anti-injection instructions barely helped (18→17/27). For comparison, an LLM with reasoning was the strongest defense (19% success). The researchers call it "directional, not a benchmark". — [Check Point Research](https://blog.checkpoint.com/ai-security/jev-is-not-a-language-model-but-it-breaks-like-one-prompt-injection-against-a-typed-decision-model/)
- Jev returns no natural-language rationale, which is a constraint for audits in regulated fields. — [DataCamp](https://www.datacamp.com/blog/system-one-models-jev); [heise](https://www.heise.de/en/news/AI-model-Jev-to-make-machines-decide-faster-11457071.html)
- HN criticism: Jev "can't emit an invalid type, it can still emit a wrong valid value", and it can do so with high confidence. The launch thread title ("40-400x cheaper and 20-200x faster") was renamed within an hour after pushback on the marketing claims. — [Firecrawl summary of the HN discussion](https://www.firecrawl.dev/blog/what-is-jev); [HN thread](https://news.ycombinator.com/item?id=49745752)
- HN praise: it was called "the missing primitive for working with llms". — [HN](https://news.ycombinator.com/item?id=49745752)
- The founder's announcement post passed 4 million views and stayed at the top of HN for most of launch day. — [Firecrawl](https://www.firecrawl.dev/blog/what-is-jev)
- Developer enthusiasm is strong. Use cases include model routing, monitoring LLM agents, and replacing LLMs for specific tasks. — [TechCrunch](https://techcrunch.com/2026/09/18/a-new-kind-of-ai-model-from-a-chatgpt-inventor-is-thrilling-developers/)

### Inferences
- For a trading bot, Jev is at most a **filter or classifier** layer (for example "is the news positive/negative", "is the regime trend or range"). Its low latency does not create an edge on 2H/4H timeframes. Probability calibration has not been independently verified on market data, and there is no evidence that it can predict price direction.
- If news or social text is passed into the state, the injection risk applies directly, and output from such input should never trigger a live order on its own.

### Gaps
- No independent, audited, long-term profitability or backtest record exists for any Jev trading use. (The traderank.ai backtest page could not be opened.)
- Reddit (r/LocalLLaMA and others) and X reaction was not reviewed directly; only the view-count figures relayed above are available.
