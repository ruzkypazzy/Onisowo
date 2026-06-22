"""Background scheduler for Àkànjí Oníṣòwò — runs daily tasks at user-specified times.

Usage (via Telegram /schedule):
  /schedule daily 9am       — run /pick every day at 9:00 UTC
  /schedule daily 9am utc   — same as above (UTC is the default)
  /schedule daily 9:30am    — run at 9:30 AM UTC
  /schedule daily 21:00     — run at 21:00 UTC (9 PM)
  /schedule stop             — cancel the scheduled task
  /schedule status           — show current schedule
"""
import logging
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _parse_time(time_str: str) -> tuple[int, int]:
    """Parse a time string like '9am', '9:30am', '21:00', '14:30'.

    Returns (hour, minute) in 24h format, in UTC.
    """
    s = time_str.strip().lower().replace("utc", "").strip()
    is_pm = "pm" in s
    is_am = "am" in s
    s = s.replace("am", "").replace("pm", "").strip()
    if ":" in s:
        h_str, m_str = s.split(":", 1)
        h, m = int(h_str), int(m_str)
    else:
        h, m = int(s), 0
    if is_pm and h < 12:
        h += 12
    if is_am and h == 12:
        h = 0
    if not (0 <= h <= 23) or not (0 <= m <= 59):
        raise ValueError(
            f"Invalid time: {time_str}. Use 9am, 9:30am, 21:00, etc."
        )
    return h, m


class DailyScheduler:
    """Background thread that runs /pick once per day at a user-specified UTC time."""

    VALID_MARKETS = ("auto", "spot", "futures", "future")

    def __init__(self, agent, chat_id: int):
        self.agent = agent
        self.chat_id = chat_id
        self.hour = 9
        self.minute = 0
        self.market = "auto"  # "auto" | "spot" | "futures"
        self.enabled = False
        self.last_run_date = None
        self.last_run_result = None
        self._thread = None
        self._stop = threading.Event()
        logger.info(f"DailyScheduler initialized for chat {chat_id}")

    def set_time(self, time_str: str) -> str:
        """Set the daily run time. Returns human-readable confirmation."""
        h, m = _parse_time(time_str)
        self.hour, self.minute = h, m
        return f"Daily pick scheduled for {h:02d}:{m:02d} UTC every day"

    def set_market(self, market: str) -> str:
        """Set the daily market. Returns human-readable confirmation."""
        m = market.strip().lower()
        if m not in self.VALID_MARKETS:
            return (
                f"❌ Unknown market: {market}\n"
                f"Valid: {', '.join(self.VALID_MARKETS)}"
            )
        self.market = m
        if m == "future":
            self.market = "futures"
        label = {
            "auto": "auto (bot decides based on BTC ADX regime)",
            "spot": "spot only",
            "futures": "futures only (with TP/SL)",
        }[self.market]
        return f"Market: {label}"

    def start(self) -> None:
        if self.enabled:
            return
        self.enabled = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info(f"Scheduler started: daily at {self.hour:02d}:{self.minute:02d} UTC")

    def stop(self) -> None:
        self.enabled = False
        self._stop.set()
        logger.info("Scheduler stopped")

    def status(self) -> str:
        market_label = {
            "auto": "auto (bot decides by BTC ADX regime)",
            "spot": "spot only",
            "futures": "futures only (with TP/SL)",
        }.get(self.market, self.market)
        if not self.enabled:
            return (
                f"⏸ Scheduler is stopped.\n"
                f"📅 Market: {market_label}\n\n"
                "Use `/schedule daily 9am` to start.\n"
                "Use `/schedule daily HH:MM` for any UTC time.\n"
                "Use `/schedule market spot` or `/schedule market futures` "
                "to lock the market."
            )
        return (
            f"⏰ Scheduler active\n"
            f"📅 Runs every day at {self.hour:02d}:{self.minute:02d} UTC\n"
            f"💱 Market: {market_label}\n"
            f"🕐 Current time (UTC): "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"✅ Last run: {self.last_run_date or 'never'}"
        )

    def _run_loop(self) -> None:
        """Wake up every 30s, check if it's time to run."""
        while not self._stop.is_set():
            try:
                now = datetime.now(timezone.utc)
                today_str = now.strftime("%Y-%m-%d")
                if (now.hour == self.hour and now.minute >= self.minute
                        and self.last_run_date != today_str):
                    self._run_pick()
                    self.last_run_date = today_str
            except Exception as e:
                logger.exception(f"Scheduler loop error: {e}")
            self._stop.wait(30)

    def _run_pick(self) -> None:
        """Run the daily pick and store the result for the user to retrieve."""
        logger.info(f"Daily pick running for chat {self.chat_id} (market={self.market})")
        try:
            from agent.core import AgentContext
            # Map market to the right command
            if self.market == "spot":
                cmd = "pickspot"
            elif self.market == "futures":
                cmd = "pickfuture"
            else:
                cmd = "pick"  # auto: let the bot decide by BTC ADX
            ctx = AgentContext(
                user_id=self.chat_id,
                user_message=f"/{cmd}",
                command=cmd,
                args={},
            )
            result = self.agent.handle(ctx)
            self.last_run_result = result
            # Persist to DB so the user can fetch it via /history or /journal
            try:
                self.agent.db.record_scheduled_run(
                    chat_id=self.chat_id,
                    result=result,
                )
            except Exception as e:
                logger.exception(f"Failed to persist scheduled run: {e}")
        except Exception as e:
            logger.exception(f"Daily pick failed: {e}")
            self.last_run_result = f"❌ Daily pick failed: {e}"
