# Oniṣòwò — Yoruba AI Trading Agent for Bitget, Powered by Qwen 3.6 Plus

> *Oniṣòwò* (oh-nee-SHAW-woh) — Yoruba for "merchant" / "trader".
> An open-source, self-hostable AI trading agent that lives in your Telegram, with **Qwen 3.6 Plus** as its brain.

**Telegram bot**: [@OnisowoBot](https://t.me/OnisowoBot)
**Hackathon**: Bitget AI Base Camp Hackathon S1 — Track 1: Trading Agent
**Built by**: [@ruzkypazzy](https://github.com/ruzkypazzy) (solo entry)
**LLM**: **Qwen 3.6 Plus** (Alibaba Cloud, via the Bitget hackathon proxy)

---

## What is this?

Oniṣòwò is an autonomous trading agent that runs in **your** Telegram, trading on **your** Bitget account, with **your** money — but driven by **Qwen 3.6 Plus**, an AI brain that uses 100+ skills to make decisions.

It's a real **trader** that thinks before it acts: it checks MEV exposure before every swap, scores counterparties for sybil risk, and gets better at trading the more it runs. Every reasoning call — *“is this a good entry?”*, *“what’s the risk here?”*, *“should I cut this position?”* — goes through Qwen.

**Why Qwen 3.6 Plus?** It’s fast, it’s smart, and it ships with $30 of free credits via the Bitget hackathon. Out of the box, the bot runs on Qwen. If you ever want to swap to a different LLM, it’s 3 env vars and a restart — but you probably won’t want to.

## The 3-min setup (anyone can do this)

```bash
# 1. Clone the repo
git clone https://github.com/ruzkypazzy/Onisowo.git
cd Onisowo

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
| `BITGET_API_KEY` | [Bitget](https://www.bitget.com) → Account → API Management → Create API Key (Read+Trade, NO Withdraw) |
| `BITGET_SECRET_KEY` | Same flow as API Key |
| `BITGET_PASSPHRASE` | You set this when creating the API key |
| `BITGET_QWEN_API_KEY` | **Qwen 3.6 Plus** credits — check the email Bitget sent you ($30 free), or grab your own from [Alibaba Cloud](https://www.alibabacloud.com) |

**Why Qwen 3.6 Plus?** It’s fast, it’s smart, and it ships with $30 of free credits via the Bitget hackathon. Every reasoning call — entry decisions, risk checks, journal reflections — goes through Qwen. We picked it because it’s the best fit for trading reasoning (fast, accurate, cost-efficient).

*(Curious about other LLMs? The bot also supports any OpenAI-compatible API. Type `/llms` in the bot to see all supported providers. But the recommended path is Qwen.)*

That's it. Your bot is live.

## What it does (commands)

| Command | What it does |
|---|---|
| `/start` | Greet Oniṣòwò, see what it can do |
| `/status` | Your portfolio + P&L |
| `/buy SOL 100` | Buy $100 of SOL (with reasoning + safety checks) |
| `/sell BTC 50` | Sell $50 of BTC |
| `/skills` | List all 100+ skills |
| `/risk` | Show current risk engine state |
| `/journal` | Recent trade journal with reasoning |
| `/help` | Full command list |

## The 100+ skills (architecture)

Oniṣòwò is composed of **100+ skills** organized by layer:

| Layer | Count | Examples |
|---|---|---|
| Core trading (Bitget API) | 15 | place_order, cancel_order, get_balance, get_ticker |
| Risk & safety | 12 | max_trade_check, drawdown_kill, exposure_cap, position_sizer |
| Onchain intelligence | 20 | mev_exposure, sybil_score, holder_analysis, contract_safety |
| Market intelligence | 15 | funding_rate, oi_delta, long_short_ratio, liquidation_heatmap |
| Strategy / decision | 15 | momentum_signal, mean_reversion_signal, edge_estimator, thesis_writer |
| Agent meta | 10 | recursive_improvement, journal_writer, prompt_tuner, confidence_calibrator |
| Telegram surface | 5 | parse_message, send_alert, ask_approval, send_chart, send_voice_summary |
| Utility | 8+ | normalize_data, retry_with_backoff, format_pnl, etc. |

Each skill is a **callable function** with input schema, output schema, and a docstring. The agent brain (**Qwen 3.6 Plus**) decides which to call, in which order, and how to combine them.

## The 4 differentiators

1. **MEV-aware execution**: before every swap, Oniṣòwò checks the MEV exposure of the proposed route. If unprotected, it routes via a private mempool.
2. **Sybil counterparty scoring**: before entering a low-cap token, it checks the top-10 holders. If 60%+ are sybil clusters, it refuses to enter.
3. **Recursive self-improvement**: every closed trade is logged with reasoning. The agent reviews its own performance weekly and tunes its strategy.
4. **Qwen 3.6 Plus as the brain**: every decision call — entry, exit, risk, journal reflection — goes through Qwen 3.6 Plus, the LLM from Alibaba Cloud. Fast, accurate, and cost-efficient. We chose Qwen because it’s the best fit for trading reasoning, and the $30 free credit from the Bitget hackathon makes it zero-cost to start. *(Bonus: the code is also LLM-agnostic, so if you want to swap to OpenAI/DeepSeek/Ollama later, it’s 3 env vars and a restart.)*

## Safety

This is **YOUR** bot, running **YOUR** code, with **YOUR** API keys. Oniṣòwò:
- **Never has access to your withdrawal permission** (you set Read+Trade only on the API key)
- **Never stores your keys in a database** (they live in `.env`, owned by you)
- **Cannot lose more than you allow** — all limits are **percentages of your balance**, not flat dollar amounts:
  - Default: max 25% of balance per trade
  - Default: max 75% of portfolio in one position
  - Default: kill switch at 30% drawdown
  - Default: 30% daily loss cap
  - These scale automatically: a $10 account gets a $2.50 max trade; a $10,000 account gets a $2,500 max trade.
  - Override per-user via `/settings max_trade_pct 50` (or any value 1–100).

## Hackathon submission

This project was built for the [Bitget AI Base Camp Hackathon S1](https://bitget-ai.gitbook.io/base-camp-hackathon-s1-en) (May 27 – June 30, 2026, $50K prize pool).

- **Track**: 1 — Trading Agent
- **Submission type**: Open-source, self-hostable
- **Demo link**: https://t.me/OnisowoBot (the live bot, public, anyone can message)
- **Submission writeup**: [see SUBMISSION.md](SUBMISSION.md)

## License

MIT — do whatever, just don't blame us if your agent gets rekt.

---

*Oniṣòwò káàlẹ́* 🛍️
