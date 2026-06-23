# Oniṣòwò — VPS Quickstart

Canonical install location: **`/opt/akanji`** on your VPS (185.2.101.34).

The repo on GitHub is `ruzkypazzy/Akanji-Onisowo` — the folder on your VPS is `akanji` because that's the systemd service name. **Don't get confused** — they're the same codebase.

---

## Day-to-day — update the live bot

```bash
cd /opt/akanji
sudo systemctl stop akanji
git pull origin main
sudo systemctl start akanji
sudo journalctl -u akanji -n 30 --no-pager    # see latest logs
```

That's it for a normal update.

---

## First-time install (if you haven't already)

```bash
# SSH into VPS (185.2.101.34)
ssh root@185.2.101.34

# Clone the repo at /opt/akanji
git clone https://github.com/ruzkypazzy/Akanji-Onisowo.git /opt/akanji
cd /opt/akanji

# Run the installer (3 min)
bash init.sh
# - Asks for your Telegram bot token
# - Asks for your Bitget API key + secret + passphrase
# - Asks for your Qwen API key
# - Sets up systemd service
```

After install:
```bash
sudo systemctl status akanji      # should say "active (running)"
sudo journalctl -u akanji -f      # live log tail
```

---

## Common commands

| What | Command |
|---|---|
| Restart bot | `sudo systemctl restart akanji` |
| Stop bot | `sudo systemctl stop akanji` |
| Start bot | `sudo systemctl start akanji` |
| Live logs | `sudo journalctl -u akanji -f` |
| Last 50 log lines | `sudo journalctl -u akanji -n 50 --no-pager` |
| Update from GitHub | `cd /opt/akanji && git pull origin main && sudo systemctl restart akanji` |
| Run smoke tests | `cd /opt/akanji && source .venv/bin/activate && python -m unittest tests.test_smoke` |
| Edit .env | `sudo nano /opt/akanji/.env` then `sudo systemctl restart akanji` |
| Find the repo (if lost) | `systemctl status akanji` (look at `WorkingDirectory=`) |

---

## If something breaks

**Bot won't start?**
```bash
sudo journalctl -u akanji -n 50 --no-pager
```
Look for the actual error (usually a missing env var or import error).

**Bitget signing error?** Make sure your API key has `Read + Trade` permissions only (never Withdraw).

**Everything is broken, start over?**
```bash
cd /opt/akanji
bash uninstall.sh        # remove service + clean files
git pull origin main     # get latest code
bash init.sh             # fresh install
```

---

## Verify it works

After `sudo systemctl status akanji` shows `active (running)`:

1. Open Telegram on your phone
2. Search for `@OnisowoBot`
3. Send `/start`
4. You should see the Yoruba greeting + the intro (no images — pure text)
5. Try `/price BTCUSDT` — should show live price
6. Try `/status` — should show your balance
7. Try `/skills` — should list **186 skills** across 10 tiers
8. Try `/risk` — should show percentage-based risk settings
9. Try `/skill ichimoku BTCUSDT` — should return Ichimoku values
10. Try `/backtest BTCUSDT momentum_breakout 30` — should return a backtest

---

## File layout on VPS

```
/opt/akanji/
├── .env                  # your secrets (chmod 600, never committed)
├── main.py               # entry point
├── init.sh               # fresh installer
├── uninstall.sh          # clean removal
├── agents.db             # SQLite (trades, journal, memory, settings)
├── agent/                # agent core (perceive → decide → execute → reflect)
├── clients/              # Bitget + Qwen API clients
├── db/                   # SQLite layer
├── risk/                 # risk engine (percentage-based, scales with balance)
├── skills/               # 186 skills in 10 tiers
│   ├── registry.py
│   └── indicators.py     # 71 technical indicators
├── tgbot/                # Telegram handler
└── tools/                # utility scripts
```

---

## Hackathon submission

- **Repo URL:** https://github.com/ruzkypazzy/Akanji-Onisowo
- **Telegram bot:** https://t.me/OnisowoBot
- **Hackathon:** [Bitget AI Base Camp S1](https://bitget-ai.gitbook.io/base-camp-hackathon-s1-en)
- **Track:** 1 — Trading Agent
