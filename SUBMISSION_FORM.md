# Bitget AI Base Camp Hackathon S1 — Submission Form (Track 1: Trading Agent)

> **Project**: Àkànjí Oníṣòwò (ah-KHAN-jee oh-nee-SHAW-woh) — *Yoruba for "Àkànjí the trader"*
> **Track**: 🟦 Track 1 — Trading Agent
> **Submitter**: ruzkypazzy (solo)
> **Bitget UID**: 7781181263
> **GitHub**: https://github.com/ruzkypazzy/Akanji-Onisowo
> **Live demo**: https://t.me/OnisowoBot
> **Brain**: Qwen 3.6 Plus (Alibaba)
> **Broker**: Bitget (UTA)

---

## 1. Project Description (4-section structure as required by the form)

### 1. Idea (Required, Highest Weight) — *Why does the strategy work?*

**The problem.** Crypto trading bots today are stuck in two camps:
- **Black-box SaaS** — custody your keys, trust their code, opaque strategies
- **Thin LLM wrappers** — prompt a model, place a market order, no trading depth

There is no open-source agent that combines (a) a 190+ skill decision library, (b) a real LLM that can reason about markets, (c) a self-critique loop that learns from losses, and (d) self-hosting with the user's own keys.

**The strategy.** Àkànjí Oníṣòwò is a Telegram-native, self-hosted AI trading agent built around a 4-stage loop:

1. **Perceive** — 71 technical indicators (RSI, MACD, ADX, Ichimoku, SuperTrend, BB, ATR, OBV, CMF, Hurst, Beta, +60 more), funding rate history, candle structure, regime classification, and live orderbook depth
2. **Decide** — Qwen 3.6 Plus is the brain. It calls the 190+ skills, gets a structured read on the market, and returns a directional pick (long / short / skip) with confidence, TP%, and SL%
3. **Execute** — Places the order on Bitget via V3 UTA endpoints, attaches TP/SL as a strategy order (one atomic call), records the trade in a local SQLite journal with the **full list of skills that fired**
4. **Reflect** — Reviews the last 7 days of closed trades, computes win rate, runs loss_autopsy (per-trade failure type), regenerates a regime-aware strategy. `/reflect` runs Qwen weekly

**Differentiators.**
- **Qwen as the brain, not the wrapper.** Every decision flows through Qwen: market read, position sizing, TP/SL, risk calls. 34 tools exposed to Qwen for the agentic pick loop.
- **190+ skills, all callable, all logged.** 71 technical indicators, regime detector, conviction decay, false-breakout detector, smart-money tracker, liquidity-depth analyzer, iceberg-order builder, funding-rate-arb detector, loss-autopsy, edge-half-life tracker, counterfactual simulator, correlation kill-switch.
- **Self-critique is the moat.** Most bots cannot learn from losses. Àkànjí runs `loss_autopsy` after every losing trade, tags the failure type (thesis_failure / execution_failure / regime_failure / bad_luck), and downweights strategies whose edges have decayed. `/reflect` runs the same loop weekly through Qwen.
- **Long + short symmetric.** A long-only bot dies in downtrends. The decision loop considers both sides; the auto-execute path honors Qwen's direction.
- **Self-hostable, your keys.** 3-minute `bash install.sh` install at `/opt/akanji` on your VPS. Bitget API key has **withdraw permission disabled** by policy. UTA supported.

**Risk controls that match the threat model.**
- 25% balance cap per trade
- 5% daily loss kill switch
- Auto-bump leverage to meet per-symbol min notional (capped at 10x)
- Anti-repeat logic penalizes recently-traded symbols
- TP/SL attached at order open, not after

### 2. Progress (Required)

**What shipped (186 → 190+ skills across 10 tiers, all live in the running bot):**
- 71 technical indicators (Ichimoku, SuperTrend, Parabolic SAR, MACD, RSI, BB, ATR, ADX, Stoch, OBV, CMF, Hurst, Beta, +57 more)
- 5-regime classifier (trending_bull, trending_bear, ranging, high_vol_chaos, low_vol_accumulation) with auto position-size and stop-loss multipliers per regime
- Adaptive TP/SL with 6-outcome decision matrix (TP hit, SL hit, thesis decay, regime flip, time decay, manual close)
- Recursive self-improvement (`/reflect` — Qwen reviews last 7 days, writes a new rule to memory)
- Loss-autopsy (per-trade failure-type tagging, recurring-pattern detection)
- Edge-half-life tracker (rolling win-rate per strategy, auto-downweight decay)
- Counterfactual simulator (3 alternative-decision simulations per closed trade, weekly Qwen review)
- Smart-money tracker (curated alpha wallets, 3+ same-token entries in 24h = strong buy signal)
- Liquidity-depth analyzer (real orderbook depth at ±1/2/5% from mid)
- Funding-rate-arb detector (perpetual funding-rate extremes, carry-trade signal)
- Iceberg-order builder (split large orders with random child orders, anti-front-run)
- Correlation kill-switch (real-time correlation matrix of open positions, force partial unwind if avg > 0.8)

**Stack:** Bitget Agent Hub (V3 UTA spot + futures + strategy-order APIs, 34 tools exposed to Qwen), **Qwen 3.6 Plus** (Alibaba Cloud, OpenAI-compatible, with auto-retry to qwen3.6-flash on transient errors), Telegram (user surface), SQLite (memory + journal), Python 3.10+, systemd service.

**Live demo URL**: https://t.me/OnisowoBot
**GitHub repo**: https://github.com/ruzkypazzy/Akanji-Onisowo (public, MIT, complete README with one-shot install)

**Next steps**: cross-margin mode for portfolio-level risk, web dashboard for non-Telegram users, onchain alpha-wallet on-chain verification (CEX-only by policy for this build).

### 3. AI Trading Thoughts (Optional — your take)

The most underrated problem in agentic trading isn't the model. It's the **feedback loop**. A model that places a trade and walks away learns nothing. A model that walks away, sees what would have happened if it had held, and rewrites its own rules next week is the one that compounds.

Àkànjí's `loss_autopsy` and `edge_half_life_tracker` are the parts I'm proudest of. They make the agent legible to itself. A losing trade becomes a tagged data point: was it a thesis failure, an execution failure, a regime failure, or bad luck? After 50 trades, the agent can tell you *which kind of loser* it tends to be, and it can downweight the strategies whose edges have decayed.

That's the moat. Not the indicators — there are 71 of them and they are not the hard part. The hard part is the agent that knows when to stop listening to itself.

### 4. (Reserved for additional links / X post / GitHub README URL)

Project description (this doc) is also published at:
- GitHub README: https://github.com/ruzkypazzy/Akanji-Onisowo/blob/main/SUBMISSION_FORM.md
- X post (placeholder — paste your own URL when posted): `<your X / Twitter post URL>`

---

## 2. Submission Links (per Track 1 requirements)

### 🟦 Track 1 — Trading Agent — REQUIRED

✅ **GitHub repo** (public, complete README, one-shot install):
   https://github.com/ruzkypazzy/Akanji-Onisowo

✅ **Live trading record / paper trading log** (timestamp, pair, side, price, size, balance change):
   https://github.com/ruzkypazzy/Akanji-Onisowo/blob/main/TRADE_LOG.md
   (or upload a `trades.csv` to the repo and paste the raw GitHub link here)

✅ **Live bot** (public, no login required to view):
   https://t.me/OnisowoBot
   - The bot is locked to the owner's Telegram user ID for trading.
   - Anyone can run these read-only commands in the bot:
     - `/about` — who Àkànjí is, the 4-stage loop, the stack
     - `/demo` — a 60-second scripted trade demo
     - `/tour` — walk through the 23 real closed trades with the full skill trail
     - `/analyze BTCUSDT 1` — the live agent loop in action. Real Qwen, real
       market data, real 17+ skill chain. Returns a recommendation with
       reasoning, TP/SL, and risks. **No real order is placed** — just
       shows what `/pick` would do.
   - All four are safe. No real money is touched.

### 🟦 Track 1 — Trading Agent — OPTIONAL

✅ **Backtest report** (with code/notebook):
   https://github.com/ruzkypazzy/Akanji-Onisowo/blob/main/scripts/backtest_demo.py
   (run: `python3 scripts/backtest_demo.py`)

✅ **Demo video** (≤3 min, public X or YouTube):
   `<paste YouTube / X post URL here when ready>`

---

## 3. Team info (form fields)

- **Team Name**: `Àkànjí Oníṣòwò` (10 words or fewer) ✅
- **Team Lead Bitget UID**: `7781181263` ✅
- **Team Lead Contact**: `<your email>` + `@<your telegram>` ✅
- **Team Members**: *(leave blank — solo entry)* ✅
- **Team Background**: *Web3 & AI developer* ✅
- **How did you hear about this hackathon**: *Bitget / Bitget AI official Twitter* + *Bitget AI Partner post* (select all that apply)

## 4. Community Impact Award (optional, separate scoring)

- **Repost of official Bitget campaign post** (required for +50 USDT Participation Award): `<paste link>`
- **Your own project post** (must include #BitgetHackathon + tag @BitgetAI): `<paste link>`

---

## TL;DR — what you copy-paste into the form

| Field | Value |
|---|---|
| Team Name | `Àkànjí Oníṣòwò` |
| Bitget UID | `7781181263` |
| Track | 🟦 Trading Agent |
| GitHub repo | `https://github.com/ruzkypazzy/Akanji-Onisowo` |
| Live trading log | `https://github.com/ruzkypazzy/Akanji-Onisowo/blob/main/TRADE_LOG.md` |
| Demo bot | `https://t.me/OnisowoBot` |
| Project description (paste from §1 above) | (the 4 sections) |
| Submission Links (paste from §2) | (the GitHub + log + video) |
| Community Impact | (your repost + your post links) |
