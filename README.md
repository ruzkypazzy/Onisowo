# Àkànjí Oníṣòwò — AI Trading Agent for Bitget, Powered by Qwen 3.6 Plus

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

## ⚡ Pick the install path that fits you

There are 4 ways to install Àkànjí. Pick the one that matches your machine and your privileges.

### Path 1: `curl | bash` on a VPS or server (with root) — most popular

You have: a Linux VPS (Contabo, DigitalOcean, AWS, etc.) with `sudo` access. You want the bot running 24/7 as a systemd service.

```bash
bash <(curl -sL https://raw.githubusercontent.com/ruzkypazzy/Akanji-Onisowo/main/install.sh)
```

The script:
1. Clones the repo to `/opt/akanji`
2. Creates a Python venv and installs dependencies
3. Prompts for 5 required + 1 recommended env vars (Telegram token, Bitget key/secret/passphrase, Qwen key, your Telegram user ID)
4. Installs a `systemd` service called `akanji` and starts it

After install:
```bash
sudo systemctl status akanji   # check status
sudo journalctl -u akanji -f   # follow logs
```

### Path 2: `curl | bash` on a Mac or Linux laptop (no root) — for evaluation

You have: macOS or Linux desktop, no `sudo`, just want to try the bot.

```bash
bash <(curl -sL https://raw.githubusercontent.com/ruzkypazzy/Akanji-Onisowo/main/install.sh) --user
```

The script:
1. Clones the repo to `~/akanji` (your home dir, no root needed)
2. Creates a Python venv and installs dependencies
3. Prompts for the same 6 env vars
4. Skips systemd (you're not root)
5. Shows you the `bash run.sh` command to start the bot

After install:
```bash
cd ~/akanji
bash run.sh                  # foreground, Ctrl+C to stop
bash run.sh --bg             # background, logs to logs/akanji.log
bash run.sh --status         # check if running
bash run.sh --logs           # tail the log file
bash run.sh --stop           # stop
```

### Path 3: `git clone` + manual setup (full control)

You have: any machine with Python 3.10+ and git. You want to see every step.

```bash
git clone https://github.com/ruzkypazzy/Akanji-Onisowo.git ~/akanji
cd ~/akanji
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
nano .env   # fill in 5 values (see env table below)
bash run.sh
```

### Path 4: Docker (no Python needed)

You have: Docker installed. You want a clean isolated run.

```bash
git clone https://github.com/ruzkypazzy/Akanji-Onisowo.git ~/akanji
cd ~/akanji
cp .env.example .env
nano .env
docker compose up -d
```

(Coming soon: the Dockerfile is in the repo. For now use Path 1, 2, or 3.)

### Which path should I pick?

| You have | Use |
|---|---|
| VPS with `sudo`, want 24/7 uptime | **Path 1** (the standard one) |
| Mac / Linux desktop, no `sudo`, just trying it | **Path 2** (with `--user` flag) |
| Want to see every step, or you're in a restricted env | **Path 3** (manual) |
| Docker, want isolation | **Path 4** (when Dockerfile ships) |

You need 6 env vars (5 required, 1 recommended):

| Variable | Required? | Where to get it |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | required | Create a bot via [@BotFather](https://t.me/BotFather), `/newbot`, get the token |
| `BITGET_API_KEY` | required | [Bitget](https://www.bitget.com) → Account → API Management → Create API Key (**Read+Trade, NO Withdraw**) |
| `BITGET_SECRET_KEY` | required | Same flow as API Key |
| `BITGET_PASSPHRASE` | required | You set this when creating the API key |
| `BITGET_QWEN_API_KEY` | required | **Qwen 3.6 Plus** credits — check the email Bitget sent you ($30 free), or grab your own from [Alibaba Cloud](https://www.alibabacloud.com) |
| `OWNER_TELEGRAM_ID` | **recommended** | Message [@userinfobot](https://t.me/userinfobot) on Telegram, copy your numeric ID. Without this, anyone who finds your bot can trade on your account. With this set, only you can run real-trade commands. |

**API key safety**: Always create your Bitget API key with **Withdraw disabled**. Àkànjí cannot withdraw your funds — only trade with them.

**Owner gate**: Set `OWNER_TELEGRAM_ID` in `.env` and restart. The bot will reject all real-trade commands from any other Telegram user, but they'll still see `/demo`, `/tour`, `/skills`, and the install command. Demo mode is automatic for everyone except you.

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

## 📜 License

MIT — do whatever, just don't blame us if your agent gets rekt.

---

## 📝 Note to Reviewer

The bot at [@OnisowoBot](https://t.me/OnisowoBot) is **temporarily open for review**. The owner has unlocked it (removed the Telegram user ID gate from `.env`) and loaded a **$10 USDT test balance** so judges can run real trades end-to-end. After the review period the owner will re-lock the bot to their Telegram user ID.

You can run any of these in Telegram:

- `/about` — who Àkànjí is, the 4-stage loop, the stack
- `/demo` — a 60-second scripted trade demo
- `/tour` — walk through real closed trades with the full skill trail
- `/analyze BTCUSDT 1` — **live agent loop**. Real Qwen, real market data, real 17+ skill chain. No real order is placed; just shows what `/pick` would do.
- `/pick`, `/pickfuture`, `/pickspot` — **real trades on the $10 test balance**. Minimum $1.01, capped at 25% of balance per trade. TP/SL attached. Use sparingly.

**Owner gate by default** (when you install your own copy): the installer prompts for `OWNER_TELEGRAM_ID` so only the owner can run real-trade commands. This is the recommended setup. The owner removed it temporarily to let judges in.

If you want to use Àkànjí in real time, install your own copy — it's open source. Pick whichever install path fits your machine:

```bash
# VPS with sudo (systemd, /opt/akanji)
bash <(curl -sL https://raw.githubusercontent.com/ruzkypazzy/Akanji-Onisowo/main/install.sh)

# Mac, Linux laptop, no-root VPS (~/akanji, no systemd)
bash <(curl -sL https://raw.githubusercontent.com/ruzkypazzy/Akanji-Onisowo/main/install.sh) --user

# Docker (no Python needed)
git clone https://github.com/ruzkypazzy/Akanji-Onisowo && cd Akanji-Onisowo
cp .env.example .env && nano .env
docker compose up -d
```

---

*Àkànjí Oníṣòwò káàlẹ́* 🪶
