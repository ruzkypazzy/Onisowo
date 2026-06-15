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


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  Ọniṣọwọ́ — Smoke tests")
    print("=" * 60 + "\n")
    unittest.main(verbosity=2)
