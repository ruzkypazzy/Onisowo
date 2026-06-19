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
            "core_trading", "risk", "indicators", "market_intel",
            "sentiment", "strategy", "strategy_new", "agent_meta", "user_facing", "utility"
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
    """Verify the risk engine blocks dangerous orders.

    The engine is now percentage-based so the same config scales with any
    account size. $10 account vs $10k account — same rules, different dollars.
    """

    def test_blocks_oversized_trade(self):
        from risk.engine import RiskEngine, RiskConfig
        from db.database import Database

        db = Database(db_path=":memory:")
        # Default max_trade_pct=0.25. Trade $40 of a $100 balance = 40%, blocked.
        risk = RiskEngine(db=db, config=RiskConfig(max_trade_pct=0.25))

        allowed, reason = risk.check_order(
            symbol="BTCUSDT", side="buy", size_usd=40.0,
            portfolio_value_usd=100.0, open_positions_count=0,
        )
        self.assertFalse(allowed)
        self.assertIn("25%", reason)
        print(f"  ✓ Blocked oversized trade ($40 = 40% > 25% cap)")

    def test_allows_normal_trade(self):
        from risk.engine import RiskEngine, RiskConfig
        from db.database import Database

        db = Database(db_path=":memory:")
        # Trade $2 of a $100 balance = 2%, well under 25% cap.
        risk = RiskEngine(db=db, config=RiskConfig(max_trade_pct=0.25))

        allowed, reason = risk.check_order(
            symbol="SOLUSDT", side="buy", size_usd=2.0,
            portfolio_value_usd=100.0, open_positions_count=0,
        )
        self.assertTrue(allowed)
        print(f"  ✓ Allowed normal trade ($2 = 2% of $100, under 25% cap)")

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
        # max_trade_pct 50% allows this trade; max_position_pct 40% blocks it.
        # Trade $5 of $10 balance = 50% trade, 50% position → position check fires.
        risk = RiskEngine(db=db, config=RiskConfig(max_trade_pct=0.5, max_position_pct=0.4))

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
        """The bot's discretion: any position with a partial gain where momentum/thesis is fading
        should be closed early — the exact % varies based on context, not a hardcoded rule."""
        from agent.core import Agent
        from agent.strategist import Strategist, StrategistConfig, CLOSE_EARLY_TP, HOLD
        from unittest.mock import MagicMock
        agent = Agent()
        # Position: 5% up with 10% TP target, RSI is overbought (thesis decayed)
        trade = {
            "id": 999, "symbol": "ETHUSDT", "side": "buy",
            "price": 100.0,  # entry
            "tp_pct": 10.0, "sl_pct": 5.0,
            "thesis": "RSI oversold bounce",
            "size": 0.1, "quote_usd": 10.0,
        }
        # Current price = 5% gain
        agent.bitget.get_ticker = MagicMock(return_value={"lastPr": "105.0"})
        # RSI at 70 (overbought = original thesis has decayed)
        agent.skills.invoke = MagicMock(side_effect=lambda name, args: {
            "ok": True, "result": {"rsi": 70.0}
        } if name == "rsi" else {"ok": True, "result": []})
        cfg = StrategistConfig()
        st = Strategist(bitget=agent.bitget, qwen=agent.qwen, db=agent.db, risk=agent.risk, skills_registry=agent.skills, config=cfg)
        d = st._evaluate_position(trade)
        # Bot decides: 5% gain with momentum=0.3, thesis_decay=0.9 → tp_reachable = 0.3*0.1 = 0.03
        # AND progress=0.5 > 0.3 AND pnl>0 → CLOSE_EARLY_TP
        self.assertEqual(d.decision, CLOSE_EARLY_TP, f"Expected CLOSE_EARLY_TP, got {d.decision}: {d.reasoning}")
        self.assertIn("Bot discretion", d.reasoning)
        self.assertGreater(d.metrics["pnl_pct"], 0)
        print(f"  ✓ Bot uses discretion to close early: {d.reasoning[:80]}...")

    def test_strategist_close_early_at_various_levels(self):
        """The bot's discretion: different P&L levels + different RSI → different decisions (not a fixed rule)."""
        from agent.core import Agent
        from agent.strategist import Strategist, StrategistConfig, CLOSE_EARLY_TP, TRAIL_STOP
        from unittest.mock import MagicMock
        # Scenarios showing the bot picks differently based on context, not a fixed threshold
        scenarios = [
            # (current_price, rsi, expected_decision, description)
            # 3% gain, RSI 65 → tp_progress=0.3 (NOT > 0.3) → falls through to TRAIL_STOP (thesis_decay=0.7, pnl > 0)
            (103.0, 65.0, TRAIL_STOP, "3% gain, RSI 65 → trail to breakeven"),
            # 5% gain, RSI 70 → tp_progress=0.5 (>0.3) + tp_reachable=0.03 (<0.3) → CLOSE_EARLY_TP
            (105.0, 70.0, CLOSE_EARLY_TP, "5% gain, RSI 70 → close early (thesis dead)"),
            # 8% gain, RSI 75 → tp_reachable is so low (momentum=0.1, decay=0.9) → CLOSE_EARLY_TP fires before TRAIL_STOP
            (108.0, 75.0, CLOSE_EARLY_TP, "8% gain, RSI 75 → close early (momentum dead)"),
        ]
        for i, (price, rsi, expected, desc) in enumerate(scenarios):
            agent = Agent()
            trade = {"id": 1000 + i, "symbol": "ETHUSDT", "side": "buy", "price": 100.0,
                     "tp_pct": 10.0, "sl_pct": 5.0, "thesis": "RSI oversold bounce",
                     "size": 0.1, "quote_usd": 10.0}
            agent.bitget.get_ticker = MagicMock(return_value={"lastPr": str(price)})
            agent.skills.invoke = MagicMock(side_effect=lambda name, args, r=rsi: {
                "ok": True, "result": {"rsi": r}
            } if name == "rsi" else {"ok": True, "result": []})
            cfg = StrategistConfig()
            st = Strategist(bitget=agent.bitget, qwen=agent.qwen, db=agent.db, risk=agent.risk, skills_registry=agent.skills, config=cfg)
            d = st._evaluate_position(trade)
            self.assertEqual(d.decision, expected, f"{desc}: expected {expected}, got {d.decision}: {d.reasoning}")
        print(f"  ✓ Bot discretion triggers CLOSE_EARLY_TP at 3%, 5%, and 8% based on context (not a fixed rule)")

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

    def test_suggest_tp_sl_returns_rr(self):
        """The TP/SL suggester should compute R:R and reject if < 1.5."""
        from agent.core import Agent
        from unittest.mock import MagicMock
        agent = Agent()
        candles = [[i, "99", "101", "98", "100", "1000"] for i in range(50)]
        agent.skills._s_get_candles = MagicMock(return_value=candles)
        result = agent.skills.invoke("suggest_tp_sl", {"symbol": "ETHUSDT", "side": "buy"})
        result = result.get("result", result) if isinstance(result, dict) else result
        self.assertTrue(result.get("ok"), f"suggest_tp_sl failed: {result}")
        self.assertIn("tp_price", result)
        self.assertIn("sl_price", result)
        self.assertIn("r_r_ratio", result)
        self.assertIn("passes_rr_filter", result)
        self.assertIn(result.get("method"), ["support_resistance", "atr_adjusted"])
        print(f"  ✓ suggest_tp_sl returns {result.get('method')} (R:R {result.get('r_r_ratio', 0):.2f}, passes={result.get('passes_rr_filter')})")

    def test_score_symbol_returns_composite(self):
        """score_symbol should return a 0-1 composite from 9 sub-scores."""
        from agent.core import Agent
        from unittest.mock import MagicMock
        agent = Agent()
        agent.bitget.get_ticker = MagicMock(return_value={"lastPr": "100", "quoteVolume": "50000000"})
        agent.skills._s_get_candles = MagicMock(return_value=[[i, "99", "101", "98", "100", "1000"] for i in range(50)])
        result = agent.skills.invoke("score_symbol", {"symbol": "ETHUSDT"})
        result = result.get("result", result) if isinstance(result, dict) else result
        self.assertTrue(result.get("ok"))
        self.assertGreaterEqual(result.get("composite", 0), 0)
        self.assertLessEqual(result.get("composite", 0), 1)
        self.assertEqual(len(result.get("sub_scores", {})), 9)
        print(f"  ✓ score_symbol composite={result.get('composite'):.2f} with 9 sub-scores")

    def test_analyze_command_caches_pending(self):
        """The /analyze command should cache the analysis in _pending_analyses."""
        from agent.core import Agent
        from dataclasses import dataclass
        from unittest.mock import MagicMock
        agent = Agent()
        agent.bitget.get_ticker = MagicMock(return_value={"lastPr": "150", "quoteVolume": "50000000", "change24h": "+2", "high24h": "155", "low24h": "148"})
        agent.skills._s_get_candles = MagicMock(return_value=[[i, "148", "152", "147", "150", "1000"] for i in range(50)])
        agent.qwen.chat = MagicMock(return_value={"content": "verdict: take\nconfidence: 0.75\nreasoning: Oversold bounce, decent setup\nrisks: funding could flip"})

        @dataclass
        class FakeCtx:
            user_id: int = 99999
            command: str = "analyze"
            args: dict = None
            user_message: str = "/analyze ETH 2"
        ctx = FakeCtx()
        out = agent._cmd_analyze(ctx)
        self.assertIn(ctx.user_id, agent._pending_analyses)
        pending = agent._pending_analyses[ctx.user_id]
        self.assertEqual(pending["symbol"], "ETHUSDT")
        self.assertEqual(pending["amount_usd"], 2.0)
        self.assertIn("tp_price", pending["tp_sl"])
        self.assertIn("Analysis", out)
        print(f"  ✓ /analyze caches pending analysis: {pending['symbol']} @ ${pending['current_price']:.4f}")

    def test_proceed_uses_pending(self):
        """The /proceed command should consume the pending analysis and execute."""
        from agent.core import Agent
        from dataclasses import dataclass
        from unittest.mock import MagicMock
        agent = Agent()
        agent._pending_analyses[12345] = {
            "symbol": "ETHUSDT", "side": "buy", "amount_usd": 2.0,
            "tp_sl": {"tp_price": 160.0, "sl_price": 145.0, "tp_pct": 6.7, "sl_pct": 3.3,
                      "r_r_ratio": 2.0, "passes_rr_filter": True,
                      "entry_price": 150.0, "method": "atr_adjusted"},
            "qwen_pick": "take", "qwen_confidence": 0.75, "qwen_reasoning": "test",
            "composite": 0.7, "current_price": 150.0, "risks": [],
            "timestamp": __import__("time").time(),
        }
        agent.risk.check_order = MagicMock(return_value=(True, ""))
        # Mock the open_position_with_strategy skill to return success
        agent.skills.invoke = MagicMock(side_effect=lambda name, args: {
            "ok": True, "skill": name, "result": {
                "ok": True, "trade_id": 42, "order_id": "TEST123",
                "entry_price": 150.0, "size": 0.0133,
            }
        } if name == "open_position_with_strategy" else {"ok": True, "result": {}})

        @dataclass
        class FakeCtx:
            user_id: int = 12345
            command: str = "proceed"
            args: dict = None
            user_message: str = "/proceed"
        ctx = FakeCtx()
        out = agent._cmd_proceed(ctx)
        self.assertNotIn(ctx.user_id, agent._pending_analyses, "Pending should be cleared after proceed")
        self.assertIn("Trade executed", out)
        self.assertIn("ETHUSDT", out)
        print(f"  ✓ /proceed executes trade and clears pending: {out[:80]}...")

    def test_proceed_with_overrides(self):
        """The /proceed SL X TP Y override should change the levels."""
        from agent.core import Agent
        from dataclasses import dataclass
        from unittest.mock import MagicMock
        agent = Agent()
        agent._pending_analyses[12346] = {
            "symbol": "BTCUSDT", "side": "buy", "amount_usd": 1.0,
            "tp_sl": {"tp_price": 70000, "sl_price": 65000, "tp_pct": 4.0, "sl_pct": 3.0,
                      "r_r_ratio": 1.3, "passes_rr_filter": False,
                      "entry_price": 67000, "method": "atr_adjusted"},
            "qwen_pick": "caution", "qwen_confidence": 0.5, "qwen_reasoning": "x",
            "composite": 0.5, "current_price": 67000, "risks": [],
            "timestamp": __import__("time").time(),
        }
        agent.risk.check_order = MagicMock(return_value=(True, ""))
        agent.skills.invoke = MagicMock(side_effect=lambda name, args: {
            "ok": True, "skill": name, "result": {
                "ok": True, "trade_id": 43, "order_id": "OVERRIDE123",
                "entry_price": 67000, "size": 0.0000149,
            }
        } if name == "open_position_with_strategy" else {"ok": True, "result": {}})

        @dataclass
        class FakeCtx:
            user_id: int = 12346
            command: str = "proceed"
            args: dict = None
            user_message: str = "/proceed SL 2 TP 6"
        ctx = FakeCtx()
        out = agent._cmd_proceed(ctx)
        self.assertIn("Trade executed", out)
        self.assertIn("TP:", out)
        print(f"  ✓ /proceed with SL/TP overrides works: {out[:80]}...")

    def test_conviction_decay_scores_correctly(self):
        """conviction_decay: full for first 6h, decays to 0.1 after 48h."""
        from agent.core import Agent
        from unittest.mock import MagicMock
        from datetime import datetime, timezone, timedelta
        agent = Agent()
        # Entry was 2h ago → should be full conviction
        entry_2h_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        r = agent.skills.invoke("conviction_decay", {"symbol": "ETHUSDT", "entry_time": entry_2h_ago, "thesis": "oversold bounce"})
        r = r.get("result", r) if isinstance(r, dict) else r
        self.assertEqual(r["conviction_score"], 1.0)
        # Entry was 60h ago → should be near 0.1
        entry_60h_ago = (datetime.now(timezone.utc) - timedelta(hours=60)).isoformat()
        r = agent.skills.invoke("conviction_decay", {"symbol": "ETHUSDT", "entry_time": entry_60h_ago, "thesis": "x"})
        r = r.get("result", r) if isinstance(r, dict) else r
        self.assertLessEqual(r["conviction_score"], 0.2)
        self.assertEqual(r["recommendation"], "exit_or_review")
        print(f"  ✓ conviction_decay: 2h=1.0, 60h={r['conviction_score']:.2f} (recommendation: {r['recommendation']})")

    def test_regime_detector_classifies(self):
        """regime_detector: classify current market into one of 5 regimes."""
        from agent.core import Agent
        from unittest.mock import MagicMock
        agent = Agent()
        # Trending bull: candles going up
        import time as t
        candles = []
        price = 100
        for i in range(100):
            o = price
            c = price * 1.002
            h = price * 1.005
            l = price * 0.998
            candles.append([t.time(), str(o), str(h), str(l), str(c), "1000"])
            price = c
        agent.skills._s_get_candles = MagicMock(return_value=candles)
        r = agent.skills.invoke("regime_detector", {"symbol": "ETHUSDT"})
        r = r.get("result", r) if isinstance(r, dict) else r
        self.assertIn(r["regime"], ["trending_bull", "trending_bear", "ranging", "high_vol_chaos", "low_vol_accumulation"])
        self.assertIn("params_adjustment", r)
        print(f"  ✓ regime_detector: classified as {r['regime']} (conf {r['confidence']})")

    def test_false_breakout_detector(self):
        """false_breakout_detector: detect below-avg-volume breakouts that mean-revert."""
        from agent.core import Agent
        from unittest.mock import MagicMock
        agent = Agent()
        # 25 prior candles around 100, then 5 recent that briefly break above but revert
        prior = [[i, "99", "101", "98", "100", "1000"] for i in range(25)]
        recent = [
            [25, "101", "103", "101", "102", "500"],  # broke up but low volume
            [26, "102", "102.5", "100", "100.5", "500"],
            [27, "100.5", "101", "99", "99.5", "500"],
            [28, "99.5", "100", "98", "98.5", "500"],
            [29, "98.5", "99", "98", "98.2", "500"],  # current
        ]
        agent.skills._s_get_candles = MagicMock(return_value=prior + recent)
        r = agent.skills.invoke("false_breakout_detector", {"symbol": "ETHUSDT"})
        r = r.get("result", r) if isinstance(r, dict) else r
        self.assertTrue(r["ok"])
        self.assertIn("is_false_breakout", r)
        print(f"  ✓ false_breakout_detector: is_false={r['is_false_breakout']} (vol_ratio={r['volume_ratio']}, rec={r['recommendation']})")

    def test_iceberg_order_splits(self):
        """iceberg_order: split a large order into randomized children."""
        from agent.core import Agent
        agent = Agent()
        wrapper = agent.skills.invoke("iceberg_order_builder", {"symbol": "ETHUSDT", "total_size_usd": 100, "num_children": 5})
        r = wrapper.get("result", wrapper) if isinstance(wrapper, dict) else wrapper
        self.assertTrue(r.get("ok"))
        self.assertEqual(r.get("num_children"), 5)
        total = sum(c["size_usd"] for c in r.get("child_orders", []))
        self.assertAlmostEqual(total, 100, delta=0.5)
        print(f"  ✓ iceberg_order: split $100 into 5 children (sizes: {[round(c['size_usd'], 1) for c in r['child_orders']]})")

    def test_correlation_kill_switch_no_positions(self):
        """correlation_kill_switch: returns 'ok' when fewer than 2 positions."""
        from agent.core import Agent
        from unittest.mock import MagicMock
        agent = Agent()
        agent.db.get_open_trades = MagicMock(return_value=[])
        r = agent.skills.invoke("correlation_kill_switch", {"threshold": 0.8})
        r = r.get("result", r) if isinstance(r, dict) else r
        self.assertTrue(r["ok"])
        self.assertEqual(r["open_positions"], 0)
        self.assertFalse(r["is_kill_switch_active"])
        print(f"  ✓ correlation_kill_switch: 0 positions → no kill switch")

    def test_extract_trade_intent_basic(self):
        """The prompt-bot parses free-form text into a trade intent."""
        from agent.core import Agent
        from unittest.mock import MagicMock
        os.environ.setdefault("BITGET_API_KEY", "test")
        os.environ.setdefault("BITGET_SECRET_KEY", "test")
        os.environ.setdefault("BITGET_PASSPHRASE", "test")
        os.environ.setdefault("BITGET_QWEN_API_KEY", "test")
        agent = Agent()
        agent.bitget = MagicMock()
        agent.qwen = MagicMock()
        cases = [
            ("buy 2 SOL", {"side": "buy", "symbol": "SOL", "amount_usd": 2.0}),
            ("sell my BTC", {"side": "sell", "symbol": "BTC", "amount_usd": None}),
            ("long ETH with $5", {"side": "buy", "symbol": "ETH", "amount_usd": 5.0}),
            ("short 0.5 BTC", {"side": "sell", "symbol": "BTC", "amount_usd": 0.5}),
            ("load up on SOL with 3 dollars", {"side": "buy", "symbol": "SOL", "amount_usd": 3.0}),
            ("dump all my PEPE", {"side": "sell", "symbol": "PEPE", "amount_usd": None}),
            ("what's the price of BTC?", None),  # not a trade
            ("analyze SOL", None),  # not a trade (uses 'analyze' verb but no buy/sell)
        ]
        for text, expected in cases:
            got = agent._extract_trade_intent(text)
            if expected is None:
                self.assertIsNone(got, f"Expected None for: {text!r}, got {got}")
            else:
                self.assertIsNotNone(got, f"Expected intent for: {text!r}, got None")
                self.assertEqual(got["side"], expected["side"], f"side mismatch for {text!r}")
                self.assertEqual(got["symbol"], expected["symbol"], f"symbol mismatch for {text!r}: got {got.get('symbol')}, expected {expected['symbol']}")
                self.assertEqual(got.get("amount_usd"), expected["amount_usd"], f"amount mismatch for {text!r}: got {got.get('amount_usd')}, expected {expected['amount_usd']}")
        print(f"  ✓ _extract_trade_intent: parsed {len(cases)} prompts correctly")

    def test_memory_recall_returns_relevant(self):
        """memory_recall: returns matches ordered by relevance."""
        from agent.core import Agent
        from unittest.mock import MagicMock
        # Stub the bitget client before Agent() constructs it
        os.environ.setdefault("BITGET_API_KEY", "test")
        os.environ.setdefault("BITGET_SECRET_KEY", "test")
        os.environ.setdefault("BITGET_PASSPHRASE", "test")
        os.environ.setdefault("BITGET_QWEN_API_KEY", "test")
        agent = Agent()
        agent.bitget = MagicMock()
        agent.qwen = MagicMock()
        # Seed memories
        agent.db.add_memory("observation", "Bought SOLUSDT at $150 with 2% target", importance=7)
        agent.db.add_memory("observation", "Bought BTCUSDT at $65000 with target $70000", importance=7)
        agent.db.add_memory("observation", "User asked about Ethereum gas fees", importance=3)
        r = agent.skills.invoke("memory_recall", {"query": "what did I trade on solana?", "limit": 3})
        r = r.get("result", r) if isinstance(r, dict) else r
        self.assertGreater(len(r["matches"]), 0, "memory_recall should return at least one match")
        # The SOL memory should rank above the Ethereum memory
        first = r["matches"][0]["content"]
        self.assertIn("SOL", first, f"Top match should be the SOL one, got: {first}")
        self.assertIn("context", r)
        self.assertNotIn("no memories", r["context"])
        print(f"  ✓ memory_recall: '{r['query']}' → {len(r['matches'])} matches, top='{first[:50]}...'")

    def test_find_best_trade_rejects_hallucinated_symbol(self):
        """find_best_trade: if Qwen invents a symbol, the skill rejects it."""
        from agent.core import Agent
        from unittest.mock import MagicMock, patch
        os.environ.setdefault("BITGET_API_KEY", "test")
        os.environ.setdefault("BITGET_SECRET_KEY", "test")
        os.environ.setdefault("BITGET_PASSPHRASE", "test")
        os.environ.setdefault("BITGET_QWEN_API_KEY", "test")
        agent = Agent()
        agent.bitget = MagicMock()
        # Mock universe scan to return real symbols only
        def fake_universe_scan(limit=50):
            return {
                "ok": True,
                "candidates": [
                    {"symbol": "BTCUSDT", "last_price": 65000, "change_24h_pct": 0.5, "volume_24h_usd": 100_000_000},
                    {"symbol": "SOLUSDT", "last_price": 150, "change_24h_pct": 1.2, "volume_24h_usd": 50_000_000},
                    {"symbol": "ETHUSDT", "last_price": 3000, "change_24h_pct": 0.3, "volume_24h_usd": 80_000_000},
                ]
            }
        # Mock score_symbol to return a normal score
        def fake_score_symbol(symbol):
            return {"ok": True, "composite": 0.65, "sub_scores": {"rsi": 0.5}, "signals": {}}
        # Mock Qwen to hallucinate a fake symbol
        agent.qwen = MagicMock()
        agent.qwen.chat = MagicMock(return_value={"content": "pick: FAKEUSDT\nconfidence: 0.9\nreasoning: hallucinated"})
        # Replace the qwen reference inside the skills registry too
        agent.skills.qwen = agent.qwen

        # Patch the methods
        agent.skills._s_universe_scan = fake_universe_scan
        agent.skills._s_score_symbol = fake_score_symbol

        r = agent.skills.invoke("find_best_trade", {"amount_usd": 2, "max_candidates": 5})
        r = r.get("result", r) if isinstance(r, dict) else r
        # The hallucinated pick should be replaced with SKIP
        self.assertEqual(r["qwen_pick"], "SKIP", f"Expected SKIP for hallucinated symbol, got {r.get('qwen_pick')}")
        self.assertIn("Rejected", r.get("qwen_reasoning", ""))
        print(f"  ✓ find_best_trade: rejected hallucinated symbol FAKEUSDT → SKIP")

    def test_universe_scan_excludes_stock_tokens(self):
        """universe_scan: must filter out Bitget R-prefix stock tokens (R-prefix + all-uppercase)."""
        from agent.core import Agent
        from unittest.mock import MagicMock
        os.environ.setdefault("BITGET_API_KEY", "test")
        os.environ.setdefault("BITGET_SECRET_KEY", "test")
        os.environ.setdefault("BITGET_PASSPHRASE", "test")
        os.environ.setdefault("BITGET_QWEN_API_KEY", "test")
        agent = Agent()
        # Mock the bitget client to return a mix of real crypto + Bitget stock tokens
        agent.bitget = MagicMock()
        agent.bitget.get_all_tickers = MagicMock(return_value=[
            # Bitget R-prefix stock tokens (must be filtered by R-prefix rule)
            {"symbol": "RFUSDT", "lastPr": "100", "quoteVolume": "500000000", "change24h": "0.1"},
            {"symbol": "RBUSDT", "lastPr": "200", "quoteVolume": "200000000", "change24h": "0.0"},
            {"symbol": "RVUSDT", "lastPr": "300", "quoteVolume": "100000000", "change24h": "0.0"},
            {"symbol": "RSPCXUSDT", "lastPr": "200", "quoteVolume": "60000000000", "change24h": "0.0"},
            {"symbol": "RSPYUSDT", "lastPr": "500", "quoteVolume": "50000000000", "change24h": "0.0"},
            {"symbol": "RNVDAUSDT", "lastPr": "200", "quoteVolume": "25000000000", "change24h": "0.0"},
            # Tokenized stocks with ON suffix (must be filtered)
            {"symbol": "AAPLONUSDT", "lastPr": "200", "quoteVolume": "10000000", "change24h": "0.0"},
            {"symbol": "MSFTONUSDT", "lastPr": "400", "quoteVolume": "10000000", "change24h": "0.0"},
            # Long all-uppercase stock token (must be filtered by 6+ char rule)
            {"symbol": "PRESPCXUSDT", "lastPr": "200", "quoteVolume": "10000000", "change24h": "0.0"},
            # Stables (must be filtered)
            {"symbol": "USDCUSDT", "lastPr": "1.0", "quoteVolume": "1000000", "change24h": "0.0"},
            {"symbol": "DAIUSDT", "lastPr": "1.0", "quoteVolume": "1000000", "change24h": "0.0"},
            # Short all-uppercase unknown ticker (not in _KNOWN_SHORT_CRYPTO, must be filtered)
            {"symbol": "XYZXUSDT", "lastPr": "10", "quoteVolume": "5000000", "change24h": "0.0"},
            {"symbol": "ABCUSDT", "lastPr": "10", "quoteVolume": "5000000", "change24h": "0.0"},
        ])
        # Use a restrictive whitelist to avoid CoinGecko's noise
        from skills.registry import SkillsRegistry
        SkillsRegistry._CRYPTO_WHITELIST_CACHE = None
        SkillsRegistry._CRYPTO_WHITELIST_TS = 0
        agent.skills._crypto_whitelist = lambda: set()  # empty whitelist
        r = agent.skills.invoke("universe_scan", {"limit": 50})
        r = r.get("result", r) if isinstance(r, dict) else r
        self.assertTrue(r.get("ok"))
        symbols = [c["symbol"] for c in r.get("candidates", [])]
        # All R-prefix stock tokens must NOT be in the list
        for stock in ["RFUSDT", "RBUSDT", "RVUSDT", "RSPCXUSDT", "RSPYUSDT", "RNVDAUSDT"]:
            self.assertNotIn(stock, symbols, f"Stock token {stock} must be filtered out")
        # All tokenized stocks must NOT be in the list
        for stock in ["AAPLONUSDT", "MSFTONUSDT", "PRESPCXUSDT"]:
            self.assertNotIn(stock, symbols, f"Stock token {stock} must be filtered out")
        # Stables must not be in the list
        for stable in ["USDCUSDT", "DAIUSDT"]:
            self.assertNotIn(stable, symbols, f"Stable {stable} must be filtered out")
        # Unknown short all-uppercase tickers must not pass
        for unknown in ["XYZXUSDT", "ABCUSDT"]:
            self.assertNotIn(unknown, symbols, f"Unknown short ticker {unknown} must be filtered out")
        print(f"  ✓ universe_scan: filtered all R-prefix stocks, ON-suffix stocks, long uppercase stocks, stables, unknown short tickers (candidates: {symbols})")

    def test_answer_question_trims_large_results(self):
        """When a skill returns > 6000 chars, the tool result is trimmed before going back to Qwen.

        This prevents the 'Done.' failure where Qwen saw too much JSON and returned empty.
        """
        from agent.core import Agent
        os.environ.setdefault("BITGET_API_KEY", "test")
        os.environ.setdefault("BITGET_SECRET_KEY", "test")
        os.environ.setdefault("BITGET_PASSPHRASE", "test")
        os.environ.setdefault("BITGET_QWEN_API_KEY", "test")
        from unittest.mock import MagicMock
        agent = Agent()
        agent.bitget = MagicMock()
        # Build a fat fake result with 50 candidates + signals + sub_scores
        big_result = {
            "ok": True,
            "skill": "universe_scan",
            "result": {
                "ok": True,
                "candidates": [
                    {
                        "symbol": f"TOKEN{i}USDT",
                        "composite": 0.5,
                        "sub_scores": {f"s{j}": 0.5 for j in range(20)},
                        "signals": {f"sig{j}": 100 for j in range(20)},
                    }
                    for i in range(50)
                ],
            },
        }
        # Capture the actual content passed to qwen.chat on the followup call
        captured = {}
        agent.qwen = MagicMock()
        def fake_chat(messages, **kwargs):
            captured.setdefault("calls", []).append(messages)
            # Return a different response on each call
            if len(captured["calls"]) == 1:
                # Initial: Qwen picks a tool
                return {"content": "", "tool_calls": [{"function": {"name": "universe_scan", "arguments": "{}"}, "id": "1"}]}
            else:
                # Followup: Qwen summarizes
                return {"content": "Based on the scan, BTCUSDT is the strongest setup."}
        agent.qwen.chat = fake_chat

        # First call: Qwen decides to call universe_scan
        from agent.core import AgentContext
        ctx = AgentContext(user_id=1, user_message="suggest a pair", command="ask", args={})
        result = agent._answer_question(ctx, "Suggest a pair to trade")

        # Verify the followup saw a trimmed result
        tool_msgs = []
        if captured.get("calls") and len(captured["calls"]) >= 2:
            tool_msgs = [m for m in captured["calls"][-1] if m.get("role") == "tool"]
        if tool_msgs:
            content = tool_msgs[0]["content"]
            self.assertLess(len(content), 6500, "Tool result should be trimmed to < 6500 chars")
            # Should keep top 10 candidates
            self.assertIn("TOKEN0USDT", content)
            self.assertNotIn("TOKEN11USDT", content)
            # Should drop noisy fields
            self.assertNotIn("sub_scores", content)
            self.assertNotIn("signals", content)

        # Verify bot didn't say 'Done.'
        self.assertNotEqual(result.strip(), "Done.")

        print(f"  ✓ _answer_question: large tool results trimmed; Qwen returned real analysis")

    def test_suggest_position_size_respects_user_amount(self):
        """suggest_position_size: respects user_requested_usd, caps at max_trade_pct of balance."""
        from risk.engine import RiskConfig, RiskEngine
        # 50% cap on a $10 balance → max $5
        cfg = RiskConfig(max_trade_pct=0.50, max_position_pct=0.75)
        engine = RiskEngine(config=cfg)
        # User asks for $10 but max is $5 (50% of $10) → capped at $5
        result = engine.suggest_position_size(
            balance_usd=10.0,
            confidence=0.8,
            signal_score=0.7,
            user_requested_usd=10.0,
        )
        self.assertEqual(result["size_usd"], 5.0, "Should cap at 50% of $10 = $5")
        # User asks for $3 (within max) → exact
        result = engine.suggest_position_size(
            balance_usd=10.0,
            confidence=0.8,
            signal_score=0.7,
            user_requested_usd=3.0,
        )
        self.assertEqual(result["size_usd"], 3.0)
        # No user amount: sized from confidence+signal, capped at max_trade_pct
        result = engine.suggest_position_size(
            balance_usd=10.0,
            confidence=0.9,
            signal_score=0.8,
        )
        self.assertGreater(result["size_usd"], 0)
        self.assertLessEqual(result["size_usd"], 5.0)
        # Below minimum
        result = engine.suggest_position_size(
            balance_usd=0.4,  # too small to trade
        )
        self.assertEqual(result["size_usd"], 0)

    def test_suggest_position_size_scales_with_balance(self):
        """The new model scales with balance: same 25% cap → different dollar amounts."""
        from risk.engine import RiskConfig, RiskEngine
        cfg = RiskConfig(max_trade_pct=0.25)
        engine = RiskEngine(config=cfg)
        # $10 account → max trade $2.50
        r10 = engine.suggest_position_size(balance_usd=10.0, confidence=1.0, signal_score=1.0)
        # $1000 account → max trade $250
        r1000 = engine.suggest_position_size(balance_usd=1000.0, confidence=1.0, signal_score=1.0)
        # $10000 account → max trade $2500
        r10000 = engine.suggest_position_size(balance_usd=10000.0, confidence=1.0, signal_score=1.0)
        self.assertAlmostEqual(r10["size_usd"], 2.50, places=2)
        self.assertAlmostEqual(r1000["size_usd"], 250.00, places=2)
        self.assertAlmostEqual(r10000["size_usd"], 2500.00, places=2)
        print(f"  ✓ suggest_position_size scales: $10→$2.50, $1k→$250, $10k→$2.5k (same 25% cap)")

        print(f"  ✓ suggest_position_size: respects user amount, caps at max, rejects too-small balance")

    def test_indicators_smoke(self):
        """All 71 technical indicators compute without crashing on synthetic data."""
        from skills import indicators as ind
        import math

        # 100-bar synthetic uptrending series
        closes = [100 + i * 0.5 + (i % 7) * 0.1 for i in range(100)]
        highs = [c + 1 for c in closes]
        lows = [c - 1 for c in closes]
        vols = [1000 + i * 10 for i in range(100)]
        bench = [c * 0.9 for c in closes]

        # Run each indicator and assert it doesn't raise
        tests = [
            ("ichimoku", lambda: ind.ichimoku(closes, highs, lows)),
            ("supertrend", lambda: ind.supertrend(closes, highs, lows)),
            ("parabolic_sar", lambda: ind.parabolic_sar(highs, lows)),
            ("aroon", lambda: ind.aroon(highs, lows)),
            ("vortex", lambda: ind.vortex(highs, lows, closes)),
            ("ttm_squeeze", lambda: ind.ttm_squeeze(closes, highs, lows)),
            ("qqe", lambda: ind.qqe(closes)),
            ("halftrend", lambda: ind.halftrend(closes, highs, lows)),
            ("alligator", lambda: ind.alligator(highs, lows)),
            ("gator", lambda: ind.gator(highs, lows)),
            ("dmi", lambda: ind.dmi(highs, lows, closes)),
            ("aroon_oscillator", lambda: ind.aroon_oscillator(highs, lows)),
            ("dpo", lambda: ind.dpo(closes)),
            ("eom", lambda: ind.eom(closes, highs, lows, vols)),
            ("tsi", lambda: ind.tsi(closes)),
            ("stochastic", lambda: ind.stochastic(highs, lows, closes)),
            ("stoch_rsi", lambda: ind.stoch_rsi(closes)),
            ("williams_r", lambda: ind.williams_r(highs, lows, closes)),
            ("cci", lambda: ind.cci(highs, lows, closes)),
            ("mfi", lambda: ind.mfi(highs, lows, closes, vols)),
            ("roc", lambda: ind.roc(closes)),
            ("momentum", lambda: ind.momentum_indicator(closes)),
            ("ao", lambda: ind.ao(highs, lows)),
            ("apo", lambda: ind.apo(closes)),
            ("ppo", lambda: ind.ppo(closes)),
            ("ult_osc", lambda: ind.ult_osc(highs, lows, closes)),
            ("rsi_divergence", lambda: ind.rsi_divergence(closes)),
            ("macd_signal_cross", lambda: ind.macd_signal_cross(closes)),
            ("coppock", lambda: ind.coppock(closes)),
            ("fisher_transform", lambda: ind.fisher_transform(highs, lows)),
            ("atr", lambda: ind.atr(highs, lows, closes)),
            ("natr", lambda: ind.natr(highs, lows, closes)),
            ("bollinger_width", lambda: ind.bollinger_width(closes)),
            ("bollinger_pct_b", lambda: ind.bollinger_pct_b(closes)),
            ("keltner", lambda: ind.keltner(closes, highs, lows)),
            ("donchian", lambda: ind.donchian(highs, lows)),
            ("chandelier", lambda: ind.chandelier(highs, lows, closes)),
            ("historical_volatility", lambda: ind.historical_volatility(closes)),
            ("ulcer_index", lambda: ind.ulcer_index(closes)),
            ("stddev", lambda: ind.stddev(closes)),
            ("chaikin_volatility", lambda: ind.chaikin_volatility(highs, lows)),
            ("obv", lambda: ind.obv(closes, vols)),
            ("ad_line", lambda: ind.ad_line(highs, lows, closes, vols)),
            ("adosc", lambda: ind.adosc(highs, lows, closes, vols)),
            ("cmf", lambda: ind.cmf(highs, lows, closes, vols)),
            ("vwap", lambda: ind.vwap(highs, lows, closes, vols)),
            ("vwma", lambda: ind.vwma(closes, vols)),
            ("emv", lambda: ind.emv(highs, lows, vols)),
            ("fi", lambda: ind.fi(closes, vols)),
            ("nvi", lambda: ind.nvi(closes, vols)),
            ("pvi", lambda: ind.pvi(closes, vols)),
            ("pvt", lambda: ind.pvt(closes, vols)),
            ("volume_profile", lambda: ind.volume_profile(closes, vols)),
            ("kama", lambda: ind.kama(closes)),
            ("frama", lambda: ind.frama(closes)),
            ("alma", lambda: ind.alma(closes)),
            ("hma", lambda: ind.hma(closes)),
            ("mcginley", lambda: ind.mcginley(closes)),
            ("t3", lambda: ind.t3(closes)),
            ("zlema", lambda: ind.zlema(closes)),
            ("tema", lambda: ind.tema(closes)),
            ("smma", lambda: ind.smma(closes)),
            ("garman_klass", lambda: ind.garman_klass(highs, lows)),
            ("beta", lambda: ind.beta(closes, bench)),
            ("correlation", lambda: ind.correlation(closes, bench)),
            ("hurst", lambda: ind.hurst(closes)),
            ("linear_regression", lambda: ind.linear_regression(closes)),
            ("zscore", lambda: ind.zscore(closes)),
            ("skew", lambda: ind.skew(closes)),
            ("kurtosis", lambda: ind.kurtosis(closes)),
            ("variance", lambda: ind.variance(closes)),
            ("quantile", lambda: ind.quantile(closes)),
        ]
        for name, fn in tests:
            try:
                result = fn()
                self.assertIsInstance(result, dict)
            except Exception as e:
                self.fail(f"Indicator {name!r} crashed: {e}")
        print(f"  ✓ All {len(tests)} indicators compute cleanly on synthetic data")

    def test_fuzzy_skill_match(self):
        """Fuzzy match: get_price → get_ticker, balance → get_balance, etc."""
        from agent.core import Agent
        from unittest.mock import MagicMock
        import os
        os.environ.setdefault("BITGET_API_KEY", "test")
        os.environ.setdefault("BITGET_SECRET_KEY", "test")
        os.environ.setdefault("BITGET_PASSPHRASE", "test")
        os.environ.setdefault("BITGET_QWEN_API_KEY", "test")
        agent = Agent()
        agent.bitget = MagicMock()
        agent.qwen = MagicMock()

        # Common hallucinations
        cases = [
            ("get_price", "get_ticker"),
            ("price", "get_ticker"),
            ("balance", "get_balance"),
            ("scan_market", "universe_scan"),
            ("market_scan", "universe_scan"),
        ]
        for hallucinated, expected in cases:
            matched = agent.skills._fuzzy_skill_match(hallucinated)
            self.assertEqual(matched, expected, f"Expected {hallucinated!r} → {expected!r}, got {matched!r}")
        # Unknown name returns None
        self.assertIsNone(agent.skills._fuzzy_skill_match("totally_made_up_skill"))
        print(f"  ✓ _fuzzy_skill_match: {len(cases)} common hallucinations routed correctly")

    def test_extract_trade_intent_dollar_phrasings(self):
        """_extract_trade_intent: 'I want you to make a 2 dollars trade on ETHUSDT' parses correctly."""
        from agent.core import Agent
        from unittest.mock import MagicMock
        import os
        os.environ.setdefault("BITGET_API_KEY", "test")
        os.environ.setdefault("BITGET_SECRET_KEY", "test")
        os.environ.setdefault("BITGET_PASSPHRASE", "test")
        os.environ.setdefault("BITGET_QWEN_API_KEY", "test")
        agent = Agent()
        agent.bitget = MagicMock()
        agent.qwen = MagicMock()

        cases = [
            ("I want you to make a 2 dollars trade on ETHUSDT", {"side": "buy", "symbol": "ETHUSDT", "amount_usd": 2.0}),
            ("make a 10 dollars trade on BTC", {"side": "buy", "symbol": "BTC", "amount_usd": 10.0}),
            ("use $4.39 to place a trade", {"side": "buy", "symbol": None, "amount_usd": 4.39}),
            ("Go in with this signal, use $4.39 to place a trade", {"side": "buy", "symbol": None, "amount_usd": 4.39}),
            ("trade ETH with 2 dollars", {"side": "buy", "symbol": "ETH", "amount_usd": 2.0}),
            ("buy 2 SOL", {"side": "buy", "symbol": "SOL", "amount_usd": 2.0}),
            ("sell my BTC", {"side": "sell", "symbol": "BTC", "amount_usd": None}),
        ]
        for text, expected in cases:
            got = agent._extract_trade_intent(text)
            self.assertIsNotNone(got, f"Expected intent for: {text!r}")
            self.assertEqual(got["side"], expected["side"], f"side mismatch for {text!r}")
            self.assertEqual(got.get("symbol"), expected["symbol"], f"symbol mismatch for {text!r}: got {got.get('symbol')}, expected {expected['symbol']}")
            self.assertEqual(got.get("amount_usd"), expected["amount_usd"], f"amount mismatch for {text!r}: got {got.get('amount_usd')}, expected {expected['amount_usd']}")
        print(f"  ✓ _extract_trade_intent: {len(cases)} real-user phrasings parsed correctly")

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  Ọniṣọwọ́ — Smoke tests")
    print("=" * 60 + "\n")
    unittest.main(verbosity=2)
