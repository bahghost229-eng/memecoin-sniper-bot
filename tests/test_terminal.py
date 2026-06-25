"""Tests du terminal de trading manuel : safety partagé, achat/vente, wallet, génération."""
import os, sys, asyncio, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import safety
from agents.manual_trader import ManualTrader


class FJup:
    def __init__(self, sellable=True, balance=None, buy_ok=True):
        self.sellable=sellable; self.balance=balance; self.buy_ok=buy_ok
        self.bought=[]; self.sold=[]
    async def is_sellable(self, mint, probe_units=1_000_000): return self.sellable
    async def get_price(self, mint, probe_units=1_000_000): return 7.0
    async def get_token_balance(self, mint): return self.balance
    async def buy(self, mint, amt, slip):
        self.bought.append((mint, amt))
        return {"success": self.buy_ok, "price": 0.0001, "out_amount": 1000,
                "tx_hash": "BUY", "error": None if self.buy_ok else "no_quote"}
    async def sell(self, mint, qty, slip):
        self.sold.append((mint, qty)); return {"success": True, "tx_hash": "SELL"}


class FHel:
    def __init__(self, freeze=None, liq=None): self.freeze=freeze; self.liq=liq
    async def get_mint_authorities(self, mint): return {"freeze_authority": self.freeze, "mint_authority": None}
    async def get_pool_liquidity_sol(self, mint, platform=None): return self.liq


class FPort:
    def __init__(self): self.positions={}
    def add_position(self, p): self.positions[p["token"]]=p
    def remove_position(self, t): self.positions.pop(t, None)
    def list_positions(self): return list(self.positions.values())


def _cfg():
    return {"trading": {"slippage_bps": 1500},
            "safety": {"require_sellable": True, "block_freeze_authority": True, "block_mint_authority": False}}

def _trader(jup, hel=None, port=None):
    return ManualTrader(_cfg(), jup, hel or FHel(), port or FPort())


class TestSafetyShared(unittest.IsolatedAsyncioTestCase):
    async def test_clean_ok(self):
        ok, _ = await safety.check_token(FHel(), FJup(), "M")
        self.assertTrue(ok)
    async def test_freeze_blocks(self):
        ok, reason = await safety.check_token(FHel(freeze="X"), FJup(), "M")
        self.assertFalse(ok); self.assertIn("freeze", reason)
    async def test_non_sellable_blocks(self):
        ok, reason = await safety.check_token(FHel(), FJup(sellable=False), "M")
        self.assertFalse(ok); self.assertIn("vente", reason)


class TestManualBuy(unittest.IsolatedAsyncioTestCase):
    async def test_buy_clean_registers_position(self):
        jup=FJup(); port=FPort(); t=_trader(jup, port=port)
        res=await t.buy("MINT", 0.1)
        self.assertTrue(res["success"])
        self.assertEqual(jup.bought, [("MINT", 0.1)])
        self.assertIn("MINT", port.positions)
    async def test_buy_blocked_freeze(self):
        jup=FJup(); port=FPort(); t=_trader(jup, hel=FHel(freeze="X"), port=port)
        res=await t.buy("MINT", 0.1)
        self.assertFalse(res["success"]); self.assertTrue(res.get("blocked"))
        self.assertEqual(jup.bought, []); self.assertNotIn("MINT", port.positions)
    async def test_buy_blocked_non_sellable(self):
        jup=FJup(sellable=False); t=_trader(jup)
        res=await t.buy("MINT", 0.1)
        self.assertTrue(res.get("blocked")); self.assertEqual(jup.bought, [])


class TestManualSell(unittest.IsolatedAsyncioTestCase):
    async def _t_with_pos(self, balance):
        jup=FJup(balance=balance); port=FPort(); t=_trader(jup, port=port)
        await t.buy("MINT", 0.1)   # enregistre la position (amount_tokens=1000)
        return jup, port, t
    async def test_sell_50pct_uses_real_balance(self):
        jup, port, t=await self._t_with_pos(balance=2000)
        res=await t.sell("MINT", 50)
        self.assertTrue(res["success"])
        self.assertEqual(jup.sold[-1], ("MINT", 1000))   # 50% de 2000
        self.assertIn("MINT", port.positions)            # pas fermée (pct<100)
    async def test_sell_100pct_closes(self):
        jup, port, t=await self._t_with_pos(balance=2000)
        await t.sell("MINT", 100)
        self.assertEqual(jup.sold[-1], ("MINT", 2000))
        self.assertNotIn("MINT", port.positions)
    async def test_sell_fallback_to_position_qty_when_balance_none(self):
        jup, port, t=await self._t_with_pos(balance=None)
        await t.sell("MINT", 100)
        self.assertEqual(jup.sold[-1], ("MINT", 1000))   # amount_tokens de la position
    async def test_sell_zero_balance_fails(self):
        jup=FJup(balance=0); t=_trader(jup)
        res=await t.sell("MINT", 100)
        self.assertFalse(res["success"]); self.assertIn("solde", res["error"])


class TestTokenInfo(unittest.IsolatedAsyncioTestCase):
    async def test_aggregates_fields(self):
        t=_trader(FJup(sellable=True), hel=FHel(freeze="X", liq=12.5))
        info=await t.token_info("MINT")
        self.assertEqual(info["mint"], "MINT")
        self.assertTrue(info["sellable"])
        self.assertTrue(info["freeze_authority"])
        self.assertEqual(info["liquidity_sol"], 12.5)
        self.assertEqual(info["price_lamports_per_unit"], 7.0)


class TestWalletHelpers(unittest.IsolatedAsyncioTestCase):
    async def test_get_sol_balance_parse(self):
        try:
            from utils.jupiter_client import JupiterClient
        except Exception as e:
            self.skipTest(f"solders indispo: {e}")
        jc=JupiterClient({"quote_url":"x","swap_url":"x","sol_mint":"SOL"},
                         {"priority_fee_lamports":0}, {"encrypted_private_key":""}, dry_run=True)
        jc.wallet_pubkey=lambda: "PUB"
        async def fake_rpc(method, params): return {"value": 2_000_000_000}
        jc._rpc_post=fake_rpc
        self.assertAlmostEqual(await jc.get_sol_balance(), 2.0)

    def test_generate_and_import(self):
        from utils import solana_wallet
        pub, secret = solana_wallet.generate_keypair()
        self.assertEqual(solana_wallet.pubkey_from_secret(secret), pub)
        pub2, _ = solana_wallet.generate_keypair()
        self.assertNotEqual(pub, pub2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
