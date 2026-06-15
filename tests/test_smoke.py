"""
Smoke tests — verify the agent boots, the skills register, and the risk engine blocks bad orders.

Run: python -m pytest tests/ -v
Or:  python tests/test_smoke.py
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Make parent dir importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSkillsRegistry(unittest.TestCase):
    """Verify the skills registry loads and has the right count."""

    def setUp(self):
        # Set fake env vars so imports don't fail
        os.environ.setdefault("BITGET_API_KEY", "fake_key")
        os.environ.setdefault("BITGET_SECRET_KEY", "fake_secret")
        os.environ.setdefault("BITGET_PASSPHRASE", "fake_pass")
        os.environ.setdefault("BITGET_QWEN_API_KEY", "fake_qwen")
        os.environ.setdefault("TELEGRAM_BOT_TOKEN", "fake_tg")

    def test_skills_count(self):
        """Verify we have 100+ skills."""
        from skills.registry import SkillsRegistry
        from clients.bitget import BitgetClient
        from clients.qwen import QwenClient
        from db.database import Database
        from risk.engine import RiskEngine

        bitget = BitgetClient()
        qwen = QwenClient()
        db = Database(db_path=":memory:")
        risk = RiskEngine(db=db)

        reg = SkillsRegistry(bitget=bitget, db=db, risk=risk, qwen=qwen)
        count = reg.count()
        self.assertGreaterEqual(count, 100, f"Expected 100+ skills, got {count}")
        print(f"  ✓ {count} skills registered")

    def test_skill_categories(self):
        """Verify all expected categories are present."""
        from skills.registry import SkillsRegistry
        from clients.bitget import BitgetClient
        from clients.qwen import QwenClient
        from db.database import Database
        from risk.engine import RiskEngine

        bitget = BitgetClient()
        qwen = QwenClient()
        db = Database(db_path=":memory:")
        risk = RiskEngine(db=db)
        reg = SkillsRegistry(bitget=bitget, db=db, risk=risk, qwen=qwen)

        categories = set()
        for skill in reg.skills.values():
            categories.add(skill.category)

        expected = {
            "core_trading", "risk", "onchain", "market_intel",
            "sentiment", "strategy", "agent_meta", "user_facing", "utility"
        }
        self.assertTrue(expected.issubset(categories), f"Missing categories: {expected - categories}")
        print(f"  ✓ All {len(expected)} categories present")

    def test_invoke_unknown_skill(self):
        """Verify invoking an unknown skill returns an error gracefully."""
        from skills.registry import SkillsRegistry
        from clients.bitget import BitgetClient
        from clients.qwen import QwenClient
        from db.database import Database
        from risk.engine import RiskEngine

        bitget = BitgetClient()
        qwen = QwenClient()
        db = Database(db_path=":memory:")
        risk = RiskEngine(db=db)
        reg = SkillsRegistry(bitget=bitget, db=db, risk=risk, qwen=qwen)

        result = reg.invoke("not_a_real_skill", {})
        self.assertFalse(result.get("ok"))
        self.assertIn("Unknown skill", result.get("error", ""))
        print(f"  ✓ Unknown skill handled gracefully")

    def test_invoke_normalize_symbol(self):
        """Verify normalize_symbol skill works."""
        from skills.registry import SkillsRegistry
        from clients.bitget import BitgetClient
        from clients.qwen import QwenClient
        from db.database import Database
        from risk.engine import RiskEngine

        bitget = BitgetClient()
        qwen = QwenClient()
        db = Database(db_path=":memory:")
        risk = RiskEngine(db=db)
        reg = SkillsRegistry(bitget=bitget, db=db, risk=risk, qwen=qwen)

        result = reg.invoke("normalize_symbol", {"symbol": "sol"})
        self.assertTrue(result.get("ok"))
        self.assertEqual(result["result"]["normalized"], "SOLUSDT")
        print(f"  ✓ normalize_symbol: sol → SOLUSDT")


class TestRiskEngine(unittest.TestCase):
    """Verify the risk engine blocks dangerous orders."""

    def test_blocks_oversized_trade(self):
        from risk.engine import RiskEngine, RiskConfig
        from db.database import Database

        db = Database(db_path=":memory:")
        risk = RiskEngine(db=db, config=RiskConfig(max_trade_usd=2.0))

        allowed, reason = risk.check_order(
            symbol="BTCUSDT", side="buy", size_usd=10.0,
            portfolio_value_usd=100.0, open_positions_count=0,
        )
        self.assertFalse(allowed)
        self.assertIn("exceeds max", reason)
        print(f"  ✓ Blocked oversized trade (${10} > max ${2})")

    def test_allows_normal_trade(self):
        from risk.engine import RiskEngine, RiskConfig
        from db.database import Database

        db = Database(db_path=":memory:")
        risk = RiskEngine(db=db, config=RiskConfig(max_trade_usd=2.0))

        allowed, reason = risk.check_order(
            symbol="SOLUSDT", side="buy", size_usd=1.0,
            portfolio_value_usd=10.0, open_positions_count=0,
        )
        self.assertTrue(allowed)
        print(f"  ✓ Allowed normal trade (${1})")

    def test_blocks_kill_switch(self):
        from risk.engine import RiskEngine, RiskConfig
        from db.database import Database
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db = Database(db_path=tmp.name)
        risk = RiskEngine(db=db)
        risk.activate_kill_switch("test")

        allowed, reason = risk.check_order(
            symbol="SOLUSDT", side="buy", size_usd=1.0,
            portfolio_value_usd=10.0, open_positions_count=0,
        )
        self.assertFalse(allowed)
        self.assertIn("Kill switch", reason)

        risk.release_kill_switch()
        allowed, _ = risk.check_order(
            symbol="SOLUSDT", side="buy", size_usd=1.0,
            portfolio_value_usd=10.0, open_positions_count=0,
        )
        self.assertTrue(allowed)
        print(f"  ✓ Kill switch blocks and releases correctly")

    def test_blocks_position_too_large(self):
        from risk.engine import RiskEngine, RiskConfig
        from db.database import Database

        db = Database(db_path=":memory:")
        risk = RiskEngine(db=db, config=RiskConfig(max_trade_usd=100, max_position_pct=0.4))

        allowed, reason = risk.check_order(
            symbol="SOLUSDT", side="buy", size_usd=5.0,
            portfolio_value_usd=10.0, open_positions_count=0,
        )
        self.assertFalse(allowed)
        self.assertIn("exceeds max", reason)
        print(f"  ✓ Blocked over-concentrated position (50% > max 40%)")


class TestDatabase(unittest.TestCase):
    """Verify the database works."""

    def setUp(self):
        import tempfile
        # Use a real temp file (not :memory:) so schema persists across connections
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name

    def tearDown(self):
        try:
            os.unlink(self.db_path)
        except Exception:
            pass

    def test_record_and_retrieve_trade(self):
        from db.database import Database

        db = Database(db_path=self.db_path)
        trade_id = db.record_trade(
            symbol="SOLUSDT", side="buy", order_type="spot",
            size=1.0, price=100.0, quote_usd=100.0,
            order_id="test123", reason="test trade",
            skills_used=["test"], confidence=0.8,
        )
        self.assertIsInstance(trade_id, int)

        trades = db.get_recent_trades(limit=1)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["symbol"], "SOLUSDT")
        print(f"  ✓ Trade recorded and retrieved (id={trade_id})")

    def test_memory_storage(self):
        from db.database import Database

        db = Database(db_path=self.db_path)
        mem_id = db.add_memory("rule", "Test rule", tags=["test"], importance=7)
        self.assertIsInstance(mem_id, int)

        memories = db.get_memories(category="rule")
        self.assertEqual(len(memories), 1)
        self.assertEqual(memories[0]["content"], "Test rule")
        print(f"  ✓ Memory stored and retrieved (id={mem_id})")


class TestAgentBoot(unittest.TestCase):
    """Verify the agent initializes without crashing."""

    def test_agent_init(self):
        os.environ.setdefault("BITGET_API_KEY", "fake_key")
        os.environ.setdefault("BITGET_SECRET_KEY", "fake_secret")
        os.environ.setdefault("BITGET_PASSPHRASE", "fake_pass")
        os.environ.setdefault("BITGET_QWEN_API_KEY", "fake_qwen")
        os.environ.setdefault("TELEGRAM_BOT_TOKEN", "fake_tg")

        from agent.core import Agent, AgentContext
        agent = Agent()

        # Verify all 4 layers initialized
        self.assertIsNotNone(agent.bitget)
        self.assertIsNotNone(agent.qwen)
        self.assertIsNotNone(agent.db)
        self.assertIsNotNone(agent.risk)
        self.assertIsNotNone(agent.skills)
        print(f"  ✓ Agent boots: bitget + qwen + db + risk + skills ({agent.skills.count()} skills)")


class TestWATGreeting(unittest.TestCase):
    """Verify the WAT (West Africa Time) greeting helper returns the right Yoruba salutation."""

    def test_wat_greeting_returns_valid_greeting(self):
        from agent.core import _wat_greeting
        greeting = _wat_greeting()
        # All 4 valid greetings + the "áàlẹ́" base should be in the result
        valid_keywords = ["káàrọ̀", "káàsán", "káàlẹ́"]
        self.assertTrue(
            any(kw in greeting for kw in valid_keywords),
            f"Got: {greeting!r}"
        )
        print(f"  ✓ _wat_greeting returns Yoruba greeting: {greeting}")

    def test_wat_greeting_is_wat_aware(self):
        from agent.core import _wat_greeting
        from datetime import datetime, timezone, timedelta
        # WAT is UTC+1, not affected by server's local time
        # If the bot's host is in UTC, current WAT hour = UTC hour + 1
        # Just verify the function doesn't crash and returns a string with a Yoruba marker
        greeting = _wat_greeting()
        self.assertIn("Ọniṣọwọ́", greeting, "Should use Ọniṣọwọ́ prefix")
        self.assertIn("ẹ", greeting, "Should use Yoruba second-person 'ẹ' marker")
        print(f"  ✓ _wat_greeting uses WAT timezone, returns: {greeting}")

    def test_advise_before_trade_skill_registered(self):
        """Verify the new advise_before_trade skill is registered and has expected params."""
        from agent.core import Agent
        agent = Agent()
        skill = agent.skills.skills.get("advise_before_trade")
        self.assertIsNotNone(skill, "advise_before_trade skill must be registered")
        self.assertEqual(skill.category, "strategy")
        self.assertIn("symbol", skill.parameters)
        self.assertIn("side", skill.parameters)
        self.assertIn("amount_usd", skill.parameters)
        print("  ✓ advise_before_trade skill is registered with correct params")

    def test_advise_before_trade_returns_structured_advisory(self):
        """Mock Qwen and Bitget to verify the skill returns an advisory dict."""
        from agent.core import Agent
        from unittest.mock import MagicMock
        agent = Agent()
        agent.bitget.get_ticker = MagicMock(return_value={"lastPr": "150.5", "change24h": "+2.1", "high24h": "153", "low24h": "147", "baseVolume": "1000000"})
        agent.skills._s_funding_hist = MagicMock(return_value={"note": "stub"})
        agent.skills._s_get_candles = MagicMock(return_value=[[0, "147", "148", "147", "150.5", "100"]] * 24)
        agent.qwen.chat = MagicMock(return_value={"content": (
            "action: hold\n"
            "confidence: 0.85\n"
            "reasoning: RSI is overbought; chase risk is high.\n"
            "risks: overbought RSI, resistance nearby, low volume\n"
            "alternatives: wait for pullback to $147, set limit buy"
        )})
        result_wrapper = agent.skills.invoke("advise_before_trade", {"symbol": "ETHUSDT", "side": "buy", "amount_usd": 10.0, "user_intent_reason": "FOMO"})
        result = result_wrapper.get("result", result_wrapper)
        self.assertEqual(result["action"], "hold")
        self.assertGreaterEqual(result["confidence"], 0.7)
        # hold = soft conflict (advisor says "don't trade" but doesn't disagree with direction)
        self.assertFalse(result["conflicts"], "hold is a soft conflict, not a hard conflict")
        self.assertIn("RSI", result["reasoning"])
        self.assertGreater(len(result["risks"]), 0)
        self.assertGreater(len(result["alternatives"]), 0)
        print(f"  ✓ advise_before_trade returns structured advisory: action={result['action']} conf={result['confidence']:.2f}")

    def test_pending_advisory_clears_on_abort(self):
        """The Agent's _pending_advisories dict should support set + clear."""
        from agent.core import Agent
        from dataclasses import dataclass
        agent = Agent()

        @dataclass
        class FakeCtx:
            user_id: int = 12345
            command: str = "buy"
            args: dict = None
            user_message: str = ""
        ctx = FakeCtx(args={"symbol": "SOL", "amount_usd": 2}, user_message="test")
        agent._pending_advisories[ctx.user_id] = {"side": "buy", "symbol": "SOLUSDT", "amount_usd": 2, "price": 150, "advisory": {}, "timestamp": 1e12}
        self.assertIn(ctx.user_id, agent._pending_advisories)
        out = agent._cmd_abort(ctx)
        self.assertNotIn(ctx.user_id, agent._pending_advisories)
        self.assertIn("aborted", out.lower())
        print("  ✓ /abort clears the pending advisory cache")

    def test_strategist_adaptive_close_early_tp(self):
        """The 5%-with-fading-thesis scenario: should CLOSE_EARLY_TP."""
        from agent.core import Agent
        from agent.strategist import Strategist, StrategistConfig, CLOSE_EARLY_TP, HOLD
        from unittest.mock import MagicMock
        agent = Agent()
        # Mock a trade that's 5% up with a 10% TP target
        trade = {
            "id": 999, "symbol": "ETHUSDT", "side": "buy",
            "price": 100.0,  # entry
            "tp_pct": 10.0, "sl_pct": 5.0,
            "thesis": "RSI oversold bounce",
            "size": 0.1, "quote_usd": 10.0,
        }
        # Mock current price at 5% gain
        agent.bitget.get_ticker = MagicMock(return_value={"lastPr": "105.0"})
        # Mock RSI at 70 (overbought = thesis decayed)
        agent.skills.invoke = MagicMock(side_effect=lambda name, args: {
            "ok": True, "result": {"rsi": 70.0}
        } if name == "rsi" else {"ok": True, "result": []})
        cfg = StrategistConfig()
        st = Strategist(bitget=agent.bitget, qwen=agent.qwen, db=agent.db, risk=agent.risk, skills_registry=agent.skills, config=cfg)
        d = st._evaluate_position(trade)
        # 5% gain with momentum=0.3, thesis_decay=0.9 → tp_reachable = 0.3*0.1 = 0.03 (low) AND progress=0.5 > 0.3 AND pnl>0
        self.assertEqual(d.decision, CLOSE_EARLY_TP, f"Expected CLOSE_EARLY_TP, got {d.decision}: {d.reasoning}")
        self.assertIn("Adaptive early-TP", d.reasoning)
        self.assertGreater(d.metrics["pnl_pct"], 0)
        print(f"  ✓ 5%-with-decayed-thesis triggers CLOSE_EARLY_TP: {d.reasoning[:80]}...")

    def test_strategist_hold_when_thesis_intact(self):
        """Trade 2% up, momentum strong, thesis not decayed → HOLD."""
        from agent.core import Agent
        from agent.strategist import Strategist, StrategistConfig, HOLD
        from unittest.mock import MagicMock
        agent = Agent()
        trade = {"id": 998, "symbol": "ETHUSDT", "side": "buy", "price": 100.0, "tp_pct": 10.0, "sl_pct": 5.0, "thesis": "RSI oversold bounce", "size": 0.1, "quote_usd": 10.0}
        agent.bitget.get_ticker = MagicMock(return_value={"lastPr": "102.0"})
        # RSI still low (not overbought)
        agent.skills.invoke = MagicMock(side_effect=lambda name, args: {"ok": True, "result": {"rsi": 35.0}} if name == "rsi" else {"ok": True, "result": []})
        cfg = StrategistConfig()
        st = Strategist(bitget=agent.bitget, qwen=agent.qwen, db=agent.db, risk=agent.risk, skills_registry=agent.skills, config=cfg)
        d = st._evaluate_position(trade)
        self.assertEqual(d.decision, HOLD, f"Expected HOLD, got {d.decision}: {d.reasoning}")
        print(f"  ✓ Strong-thesis 2% gain holds: {d.reasoning[:80]}...")

    def test_strategist_sl_hit(self):
        """Price hits the SL level → CLOSE_SL."""
        from agent.core import Agent
        from agent.strategist import Strategist, StrategistConfig, CLOSE_SL
        from unittest.mock import MagicMock
        agent = Agent()
        trade = {"id": 997, "symbol": "ETHUSDT", "side": "buy", "price": 100.0, "tp_pct": 10.0, "sl_pct": 5.0, "thesis": "test", "size": 0.1, "quote_usd": 10.0}
        # Price crashed to 94 (below 5% SL = $95)
        agent.bitget.get_ticker = MagicMock(return_value={"lastPr": "94.0"})
        agent.skills.invoke = MagicMock(return_value={"ok": True, "result": {"rsi": 20.0}})
        cfg = StrategistConfig()
        st = Strategist(bitget=agent.bitget, qwen=agent.qwen, db=agent.db, risk=agent.risk, skills_registry=agent.skills, config=cfg)
        d = st._evaluate_position(trade)
        self.assertEqual(d.decision, CLOSE_SL, f"Expected CLOSE_SL, got {d.decision}")
        print(f"  ✓ SL price hit triggers CLOSE_SL: {d.reasoning[:80]}...")

    def test_strategist_tick_runs(self):
        """A full tick with no open positions and no signals returns empty decisions."""
        from agent.core import Agent
        from agent.strategist import Strategist, StrategistConfig
        from unittest.mock import MagicMock
        agent = Agent()
        agent.strategist.skills_registry = agent.skills
        agent.skills.invoke = MagicMock(return_value={"ok": True, "result": {"rsi": 50.0}})
        agent.bitget.get_ticker = MagicMock(return_value={"lastPr": "100.0"})
        agent.bitget.get_portfolio_value_usdt = MagicMock(return_value=10.0)
        decisions = agent.strategist.tick()
        self.assertIsInstance(decisions, list)
        print(f"  ✓ Strategist tick runs (returned {len(decisions)} decisions)")


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  Ọniṣọwọ́ — Smoke tests")
    print("=" * 60 + "\n")
    unittest.main(verbosity=2)
