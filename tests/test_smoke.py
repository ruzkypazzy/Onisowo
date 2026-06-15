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


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  Ọniṣọwọ́ — Smoke tests")
    print("=" * 60 + "\n")
    unittest.main(verbosity=2)
