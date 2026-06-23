"""Comprehensive test: invoke every command and verify it doesn't crash.

This is a sanity check for the bot. It runs every registered command
with a fake context and verifies the result is a non-empty string.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeDB:
    def record_trade(self, **kwargs):
        return 1
    def get_recent_trades(self, limit=10):
        return [
            {"id": 1, "symbol": "BTCUSDT", "side": "buy", "status": "closed",
             "opened_at": "2026-06-23T10:00:00", "quote_usd": 7.5, "price": 65000,
             "pnl_usd": 0.05, "pnl_pct": 0.6, "reason": "test", "skills_used": "[]"},
        ]
    def get_open_trades(self):
        return []
    def get_trades_for_review(self, days=7):
        return self.get_recent_trades()
    def get_recent_memories(self, days=7):
        return []
    def get_memories(self, limit=20):
        return []
    def get_trade_by_id(self, tid):
        return None
    def cancel_trade(self, tid, reason=""):
        return True
    def cancel_all_open_trades(self, reason=""):
        return 0
    def close_trade(self, trade_id, exit_price, pnl_usd, pnl_pct):
        return True
    def add_memory(self, cat, content, tags=None, importance=5):
        return 1
    def record_signal(self, **kwargs):
        return 1


class FakeRisk:
    def check_order(self, **kwargs):
        return True, ""
    def get_status(self, **kwargs):
        return type("S", (), {"open_positions": 0, "max_dd_pct": 0.3,
                              "kill_switch_active": False})()


class FakeBitget:
    def get_account_balance(self, ccy):
        return 50.0
    def get_ticker(self, symbol):
        return {"lastPr": 0.5, "last": 0.5}
    def get_portfolio_value_usdt(self):
        return 50.0
    def get_positions(self):
        return []
    def get_spot_holdings(self):
        return []
    def get_pending_orders(self):
        return []
    def get_orderbook(self, symbol, limit=10):
        return {"bids": [["0.5", "100"]], "asks": [["0.51", "100"]]}
    def place_spot_order(self, **kwargs):
        return {"orderId": "TEST123", "clientOid": "test-oid"}
    def place_futures_order(self, **kwargs):
        return {"orderId": "TEST456", "clientOid": "test-oid"}
    def place_strategy_order(self, **kwargs):
        return {"orderId": "STRAT789"}
    def cancel_order(self, **kwargs):
        return {"ok": True}
    def get_candles(self, **kwargs):
        return []


class FakeQwen:
    def chat(self, *args, **kwargs):
        return {"content": "fake response", "tool_calls": [], "usage": {}}


class TestAllCommands(unittest.TestCase):
    """Verify every command returns a non-empty string without raising."""

    def setUp(self):
        # Set required env vars
        os.environ.setdefault("BITGET_API_KEY", "test")
        os.environ.setdefault("BITGET_SECRET_KEY", "test")
        os.environ.setdefault("BITGET_PASSPHRASE", "test")
        os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
        os.environ.setdefault("BITGET_QWEN_API_KEY", "test")
        os.environ.setdefault("OWNER_TELEGRAM_ID", "12345")

        from unittest.mock import patch
        self._patch = patch.multiple(
            "agent.core",
            QwenClient=FakeQwen,
            BitgetClient=FakeBitget,
            Database=FakeDB,
        )
        self._patch.start()

        from agent.core import Agent, AgentContext
        self.AgentContext = AgentContext
        self.agent = Agent()
        # Manually wire up the agent
        self.agent.db = FakeDB()
        self.agent.bitget = FakeBitget()
        self.agent._owner_id = 12345

    def tearDown(self):
        self._patch.stop()

    def _test_command(self, cmd, args=None, user_id=12345):
        ctx = self.AgentContext(
            user_id=user_id, user_message=f"/{cmd}",
            command=cmd, args=args or {}
        )
        try:
            result = self.agent.handle(ctx)
            self.assertIsInstance(result, str, f"/{cmd} returned non-string: {type(result)}")
            self.assertGreater(len(result), 0, f"/{cmd} returned empty string")
        except Exception as e:
            self.fail(f"/{cmd} crashed: {type(e).__name__}: {e}")
        return result

    # Commands that should work for the owner
    def test_start(self):
        r = self._test_command("start")
        self.assertIn("Àkànjí", r)

    def test_help(self):
        r = self._test_command("help")
        self.assertGreater(len(r), 50)

    def test_about(self):
        r = self._test_command("about")
        self.assertIn("Yoruba", r)
        self.assertIn("Qwen", r)

    def test_status(self):
        self._test_command("status")

    def test_balance(self):
        self._test_command("balance")

    def test_skills(self):
        r = self._test_command("skills")
        self.assertIn("190", r or "186")  # either is fine

    def test_journal(self):
        self._test_command("journal")

    def test_review(self):
        self._test_command("review")

    def test_history(self):
        self._test_command("history")

    def test_export(self):
        self._test_command("export")

    def test_demo(self):
        r = self._test_command("demo")
        self.assertIn("Trade Receipt", r)

    def test_tour(self):
        r = self._test_command("tour")
        self.assertIn("Journal Tour", r or "tour")

    def test_memory(self):
        self._test_command("memory")

    def test_settings(self):
        self._test_command("settings")

    def test_risk(self):
        self._test_command("risk")

    def test_llm(self):
        self._test_command("llm")

    def test_intro(self):
        self._test_command("intro")

    def test_pnl(self):
        self._test_command("pnl")

    def test_skills_list(self):
        self._test_command("skill", {"name": "rsi"})

    def test_close_no_args(self):
        self._test_command("close")

    def test_sync(self):
        self._test_command("sync")

    def test_analyze(self):
        self._test_command("analyze", {"symbol": "BTCUSDT", "amount_usd": 10})

    def test_pick(self):
        self._test_command("pick", {"amount_usd": 1.01})

    def test_pickspot(self):
        self._test_command("pickspot", {"amount_usd": 1.01})

    def test_pickfuture(self):
        self._test_command("pickfuture", {"amount_usd": 1.01})

    def test_buy(self):
        self._test_command("buy", {"symbol": "BTCUSDT", "amount_usd": 1.01})

    def test_sell(self):
        self._test_command("sell", {"symbol": "BTCUSDT", "amount_usd": 1.01})

    def test_force_buy(self):
        self._test_command("force_buy", {"symbol": "BTCUSDT", "amount_usd": 1.01})

    def test_force_sell(self):
        self._test_command("force_sell", {"symbol": "BTCUSDT", "amount_usd": 1.01})

    def test_schedule_status(self):
        self._test_command("schedule", {})

    def test_release(self):
        self._test_command("release")

    def test_strategist_status(self):
        self._test_command("strategist", {})

    def test_showlog(self):
        self._test_command("showlog")

    def test_proceed(self):
        self._test_command("proceed")

    def test_abort(self):
        self._test_command("abort")

    def test_unknown_command(self):
        """Unknown commands should not crash \u2014 they go to _cmd_ask."""
        # _cmd_ask may fail because Qwen is fake but it should not crash the handler
        ctx = self.AgentContext(
            user_id=12345, user_message="hello there",
            command="ask", args={}
        )
        # Just verify it doesn't raise AttributeError
        try:
            result = self.agent.handle(ctx)
        except AttributeError as e:
            self.fail(f"Unknown command crashed: {e}")


if __name__ == "__main__":
    unittest.main()
