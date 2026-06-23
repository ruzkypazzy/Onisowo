# Àkànjí Oníṣòwò — Yoruba AI Trading Agent for Bitget, Powered by Qwen 3.6 Plus

> *Àkànjí Oníṣòwò* (ah-KHAN-jee oh-nee-SHAW-woh) — Yoruba for *"Àkànjí the trader."*
> An open-source, self-hostable AI trading agent that lives in your Telegram, with **Qwen 3.6 Plus** as its brain.

**Telegram bot**: [@OnisowoBot](https://t.me/OnisowoBot)
**Hackathon**: Bitget AI Base Camp Hackathon S1 — Track 1: Trading Agent
**Built by**: [@ruzkypazzy](https://github.com/ruzkypazzy) (solo entry)
**LLM**: **Qwen 3.6 Plus** (Alibaba Cloud, via the Bitget hackathon proxy)

---

## 🪶 The origin story

*Àkànjí* is a Yoruba name. *Oníṣòwò* means *trader* — so *Àkànjí Oníṣòwò* literally means *"Àkànjí is a trader."*

Àkànjí is a real man. A living identity. A seasoned trader — from the physical markets of West Africa to global financial floors, and now deep in the Web3 space. Decades of experience. One constant truth: he sees the market before the market moves.

This bot is the AI embodiment of that spirit. It doesn't talk about trading — *it trades*. One command and it scans 100 pairs, scores 9 signals, picks the winner, sizes the position with risk math, sets TP/SL, executes, journals, and reports. No menu-juggling, no "type the symbol" — just say `/pick` and watch it work.

You talk to it the way you'd talk to a senior trader: short, direct, in plain text. It talks back in WAT (UTC+1) with Yoruba time-of-day salutations (káàrọ̀ / káàsán / káàlẹ́ / káàlẹ́ òru).

---

## 🚀 What is this?

Àkànjí is an **autonomous trading agent** that runs in **your** Telegram, trading on **your** Bitget account, with **your** money — but driven by **Qwen 3.6 Plus**, an AI brain that uses **186 skills** to make decisions.

It's not a chatbot with a toolbox. It's a real **decision-making trader** that:

- **Scans 100+ USDT pairs** by 24h volume
- **Scores each** with 9-signal composite (RSI, MACD, EMA cross, Bollinger, ATR, ADX, support/resistance, volume trend, narrative momentum)
- **Picks the winner** — spot or futures, decided by BTC ADX regime (trending → futures, ranging → spot)
- **Sizes the position** with percentage-based risk math (max 25% per trade, 75% portfolio cap, 30% drawdown kill switch)
- **Attaches TP/SL** in one atomic call (V3 strategy order endpoint)
- **Executes the trade** on Bitget (read+trade API, no withdraw permission)
- **Journals the trade** with thesis, market, TP/SL
- **Reviews itself** weekly (P&L summary, win rate, top winners/losers, recursive reflection)

**Why Qwen 3.6 Plus?** It's fast, it's smart, and it ships with $30 of free credits via the Bitget hackathon. Every reasoning call — *is this a good entry?* / *what's the risk?* / *should I cut this position?* — goes through Qwen. We picked it because it's the best fit for trading reasoning (fast, accurate, cost-efficient).

---

## 🎯 What makes Àkànjí unique

### 1. Decision bot, not chatbot
You issue **one command** like `/pick` or `/autotrade` and Àkànjí scans, scores, decides, sizes, executes, and reports. No 12-step "menu of actions." The bot is the analyst; the human is the supervisor.

### 2. 186 skills across 10 tiers
The agent exposes 186 callable skills to Qwen as tools. Some highlights:

- **71 technical indicators**: RSI, MACD, EMA cross, Bollinger Bands, ATR, ADX, OBV, MFI, CCI, Williams %R, Stochastic RSI, Ichimoku, VWAP, Keltner, Donchian, supertrend, parabolic SAR, and many more
- **Multi-agent debate**: bull case, bear case, risk case — synthesized into one decision
- **Loss autopsy**: every losing trade gets a Qwen post-mortem that tags the failure type (thesis / execution / regime / bad luck)
- **Regime detection**: 5-regime classifier (trending_bull, trending_bear, ranging, high_vol_chaos, low_vol_accumulation) — strategies auto-adjust per regime
- **Conviction decay**: tracks how long a thesis has been held, decays confidence after 48h
- **Smart-money tracker**: watches curated alpha wallets for accumulation signals
- **Liquidity depth analyzer**: measures real orderbook depth at ±1/2/5%, estimates slippage
- **Iceberg order builder**: splits large orders into randomized child orders to avoid front-running
- **Correlation kill switch**: monitors pairwise correlation, forces partial unwind if > 0.8
- **Counterfactual simulator**: after each trade, simulates 3 alternative decisions for self-bias detection

### 3. Real production-grade code
- **41 unit tests, all green** (smoke tests for every command, every fix, every endpoint)
- **V3 + V2 fallback** for every Bitget endpoint (UTA-compatible)
- **Atomic TP/SL** via `/api/v3/trade/place-strategy-order` (one call, not three)
- **Auto-bump leverage** to meet per-symbol minimum order quantities
- **Defensive error handling**: empty ticker → fall back to plain order; strategy order fails → fall back to plain order with separate TP/SL

### 4. Cultural identity
- Yoruba time-of-day salutations (káàrọ̀ / káàsán / káàlẹ́ / káàlẹ́ òru) based on WAT
- Trade summaries that read like a senior trader talking, not a CSV report
- The agent talks *with* you, not *at* you — short, direct, and culturally grounded

---

## 🛠 What problems it solves for Bitget users

| Problem | How Àkànjí solves it |
|---|---|
| **Too many pairs, no time to scan** | Scans top 100 USDT pairs by volume in seconds |
| **Analysis paralysis** | 9-signal composite score gives a single number per pair |
| **Wrong position size** | Percentage-based risk engine that scales with your account |
| **Forgotten TP/SL** | Atomic strategy order — TP and SL attached at open |
| **No journal** | Every trade auto-recorded with thesis, market, TP/SL, order ID |
| **No reflection loop** | Weekly `/reflect` does a Qwen-written self-critique |
| **Manual sizing for minimums** | Auto-bumps leverage to meet per-symbol minimums |
| **Front-running risk on big orders** | Iceberg order builder splits orders into randomized children |
| **Correlation blowup** | Monitors pairwise correlation, kills correlated positions |
| **Telegram UX** | Runs entirely in your phone via Termius + Telegram — no desktop needed |

---

## 🧠 The 186 skills (categorized)

### Core decision-making (15)
- `pick_best_trade` — scans universe, scores, picks winner
- `analyze_symbol` — deep technical analysis
- `score_symbol` — 9-signal composite
- `conviction_decay` — track thesis half-life
- `regime_detector` — 5-regime classifier
- `narrative_momentum_scorer` — narrative arc trajectory
- `false_breakout_detector` — trap detection
- `universe_scan` — top 100 USDT pairs
- `atr`, `adx`, `support_resistance_levels` — primitives
- `suggest_tp_sl` — auto TP/SL

### Onchain intelligence (4)
- `smart_money_tracker`, `liquidity_depth_analyzer`, `unlock_calendar`, `bridge_flow_monitor`

### Execution (3)
- `order_timing_optimizer`, `iceberg_order_builder`, `funding_rate_arb_detector`

### Self-improvement (3)
- `loss_autopsy`, `edge_half_life_tracker`, `counterfactual_simulator`

### Risk (1)
- `correlation_kill_switch`

### Technical indicators (71)
- RSI, MACD, EMA cross, Bollinger Bands, ATR, ADX, OBV, MFI, CCI, Williams %R, Stochastic RSI, Ichimoku, VWAP, Keltner Channels, Donchian Channels, supertrend, parabolic SAR, and many more

### Trading actions (8)
- `place_spot_order`, `place_futures_order`, `place_futures_with_tpsl`, `place_spot_order_with_tracking`, `place_futures_order_with_tracking`, `cancel_order`, `close_position`, `record_trade`

### Market data (12)
- `get_ticker`, `get_all_tickers`, `get_candles`, `get_orderbook`, `get_account_balance`, `get_positions`, `get_open_orders`, `get_fills`, `get_instrument`, etc.

### Utilities (the rest)
- Memory, journal, risk, settings, kill switch, time, etc.

**Total: 186 skills** (15 deep Qwen tools, 171 callable).

---

## 💡 How useful is Àkànjí to Bitget?

- **For new users**: `/pick` gives them a real trade in 30 seconds — no need to learn TA, order types, or risk math first
- **For active traders**: `/pickfuture 3trades` scans, picks, and executes 3 leveraged trades with TP/SL in one command
- **For busy people**: `/schedule daily 9am` makes Àkànjí pick a trade every morning at 9 AM UTC
- **For cost-conscious users**: percentage-based risk means a $10 account works the same as a $10,000 account
- **For Bitget's brand**: a fully on-brand AI agent (Yoruba cultural grounding, decision-bot UX, real production code) is a great showcase for Bitget's API

Àkànjí is built to **showcase what Bitget's API can do when paired with a real reasoning model**. It uses V3 endpoints, UTA (Unified Trading Account), strategy orders, market data, and every other API surface Bitget ships.

---

## ⚡ The 3-min setup (anyone can do this)

```bash
# 1. Clone the repo
git clone https://github.com/ruzkypazzy/Akanji-Onisowo.git
cd Akanji-Onisowo

# 2. Install deps
pip install -r requirements.txt

# 3. Set your env vars
cp .env.example .env
nano .env   # fill in 5 values (see below)

# 4. Run the bot
python main.py
```

You need 5 env vars (all yours, never shared):

| Variable | Where to get it |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Create a bot via [@BotFather](https://t.me/BotFather), `/newbot`, get the token |
| `BITGET_API_KEY` | [Bitget](https://www.bitget.com) → Account → API Management → Create API Key (**Read+Trade, NO Withdraw**) |
| `BITGET_SECRET_KEY` | Same flow as API Key |
| `BITGET_PASSPHRASE` | You set this when creating the API key |
| `BITGET_QWEN_API_KEY` | **Qwen 3.6 Plus** credits — check the email Bitget sent you ($30 free), or grab your own from [Alibaba Cloud](https://www.alibabacloud.com) |

**API key safety**: Always create your Bitget API key with **Withdraw disabled**. Àkànjí cannot withdraw your funds — only trade with them.

---

## 🪄 One-line install (copy-paste this to any LLM)

If you want any LLM to install Àkànjí for you, paste this prompt:

> "I want to install **Àkànjí Oníṣòwò**, an open-source Bitget AI trading agent that runs in Telegram. Here's the install command — run it and then help me configure it:
>
> ```bash
> bash <(curl -sL https://raw.githubusercontent.com/ruzkypazzy/Akanji-Onisowo/main/install.sh)
> ```
>
> It will:
> 1. Clone the repo to `/opt/akanji`
> 2. Create a Python venv and install deps
> 3. Generate `.env` from `.env.example`
> 4. Ask me for: `TELEGRAM_BOT_TOKEN` (from @BotFather), `BITGET_API_KEY`, `BITGET_SECRET_KEY`, `BITGET_PASSPHRASE` (from Bitget API management, **Withdraw disabled**), and `BITGET_QWEN_API_KEY` (Qwen 3.6 Plus).
> 5. Run the bot as a systemd service called `akanji` and start it
>
> Repo: https://github.com/ruzkypazzy/Akanji-Onisowo
> Help: https://github.com/ruzkypazzy/Akanji-Onisowo/wiki"

### Or run the install script directly

```bash
bash <(curl -sL https://raw.githubusercontent.com/ruzkypazzy/Akanji-Onisowo/main/install.sh)
```

The script:
1. Clones the repo to `/opt/akanji`
2. Creates a Python venv and installs `requirements.txt`
3. Generates `.env` from `.env.example`
4. Prompts for: `TELEGRAM_BOT_TOKEN`, `BITGET_API_KEY`, `BITGET_SECRET_KEY`, `BITGET_PASSPHRASE`, `BITGET_QWEN_API_KEY`
5. Installs a `systemd` service called `akanji` and starts it

### Manual install (if you prefer)

```bash
git clone https://github.com/ruzkypazzy/Akanji-Onisowo.git /opt/akanji
cd /opt/akanji
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
python main.py
```

### systemd service (optional but recommended)

```bash
sudo tee /etc/systemd/system/akanji.service > /dev/null <<EOF
[Unit]
Description=Àkànjí Oníṣòwò — Yoruba AI Trading Agent
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=/opt/akanji
ExecStart=/opt/akanji/.venv/bin/python main.py
Restart=always
RestartSec=10
EnvironmentFile=/opt/akanji/.env

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now akanji
sudo systemctl status akanji
```

Manage it with:
```bash
sudo systemctl start akanji
sudo systemctl stop akanji
sudo systemctl restart akanji
sudo systemctl status akanji
sudo journalctl -u akanji -f    # live logs
```

---

## 🎮 The commands (the whole menu)

### Trade picking (the **decision bot** commands)
- `/pick` — scan universe, pick the best trade, auto spot/futures by regime
- `/pick 3trades` — pick 3 different trades (anti-repeat)
- `/pickfuture` — force futures with TP/SL
- `/pickspot` — force spot
- `/pick $5` or `/pick 5usdt` — pick a trade with $5 USDT
- `/daily` — alias for `/pick`
- `/autotrade` — fully autonomous: scan + pick + execute
- `/schedule daily 9am` — run `/pick` every day at 9 AM UTC
- `/schedule daily 9am spot` — daily spot only
- `/schedule daily 9am futures` — daily futures only (with TP/SL)
- `/schedule daily 9am auto` — daily, bot decides market
- `/schedule market spot` — lock market to spot (no time change)
- `/schedule market futures` — lock market to futures
- `/schedule market auto` — let the bot decide
- `/schedule stop` — cancel the schedule
- `/schedule status` — show current schedule

### Manual trading (with advisor)
- `/buy $5 SYMBOL` — buy $5 of SYMBOL with advisor
- `/sell $5 SYMBOL` — sell with advisor
- `/analyze SYMBOL` — deep analysis + bot's TP/SL suggestion
- `/proceed` — execute the pending advisory
- `/abort` — cancel a pending advisory

### Portfolio + history
- `/status` — portfolio + balance
- `/balance` — USDT balance only
- `/positions` — open positions with adaptive TP/SL
- `/history` — 7-day P&L summary
- `/review` — 7-day review (P&L, win rate, top winners/losers)
- `/export` — export trade history to a text file
- `/journal` — recent trade journal

### Strategy + risk
- `/strategy` — show current strategy rules
- `/risk` — risk engine state
- `/settings` — adjust limits (max trade %, drawdown cap, etc.)
- `/kill` — activate kill switch
- `/release` — release kill switch

### Self-awareness
- `/skills` — list all 186 skills
- `/skill <name>` — invoke a specific skill
- `/reflect` — recursive self-improvement (Qwen-written reflection)
- `/memory` — show memory
- `/strategist start` — start the autonomous loop
- `/strategist stop` — stop the autonomous loop
- `/strategist status` — show strategist status
- `/strategist tick` — force a strategist decision now

### Àkànjí identity
- `/start` — Àkànjí greeting
- `/intro` — full origin story
- `/about` — about this bot
- `/help` — all commands
- `/time` — WAT time + Yoruba greeting
- `/llm` — which LLM is powering me
- `/llms` — supported LLM providers

---

## 🛡 Safety guarantees

- **Withdraw permission is disabled** on the API key — Àkànjí cannot move your funds off Bitget
- **Percentage-based risk** — max 25% per trade, max 75% portfolio in one position, kill switch at 30% drawdown
- **Daily loss cap** at 30% — bot stops trading for the day
- **Per-trade min $1.01 USDT** — to avoid dust orders that Bitget rejects
- **Per-trade min notional $7 USDT** — above Bitget's $5 futures minimum with buffer
- **Atomic TP/SL** — both attached in one call so the position can never be left unprotected
- **Anti-repeat** — penalizes recently-traded symbols so the bot doesn't churn

---

## 🧪 Tested

- **41 unit tests, all green** (smoke tests for every command, every fix, every endpoint)
- **Live trade executed**: SOLUSDT 8x LONG futures, orderId `1452635629434400769`, $1.01 margin, $8.08 notional
- **V3 + V2 fallback** for every Bitget endpoint
- **UTA-compatible** (Unified Trading Account) — works on the latest Bitget account tier

---

## 🏆 Hackathon submission

This project was built for the [Bitget AI Base Camp Hackathon S1](https://bitget-ai.gitbook.io/base-camp-hackathon-s1-en) (May 27 – June 30, 2026, $50K prize pool).

- **Track**: 1 — Trading Agent
- **Submission type**: Open-source, self-hostable
- **Demo link**: https://t.me/OnisowoBot (the live bot, public, anyone can message)
- **Repo**: https://github.com/ruzkypazzy/Akanji-Onisowo
- **Submission writeup**: [see SUBMISSION.md](SUBMISSION.md)

---

## 📜 License

MIT — do whatever, just don't blame us if your agent gets rekt.

---

*Àkànjí Oníṣòwò káàlẹ́* 🪶
