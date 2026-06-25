"""Tests des correctifs critiques : PnL/units, garde-fous sécurité, passphrase, détecteurs."""
import os, sys, asyncio, unittest, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import base58
from utils import detectors as D
from utils.wallet_graph import WalletGraph
from utils.crypto import encrypt_private_key, decrypt_private_key, get_passphrase
from agents.portfolio_manager import PortfolioManager
from agents.token_sniper import TokenSniper


# ---------- Fakes ----------
class FakeJupiter:
    def __init__(self, value_sol=0.1, impact=0.0, sellable=True, balance=None):
        self.value_sol=value_sol; self.impact=impact; self.sellable=sellable; self.balance=balance
        self.sold=[]; self.sold_qty=[]; self.bought=[]
    async def get_position_value(self, mint, amount_tokens):
        return {"value_sol": self.value_sol, "price_impact_pct": self.impact}
    async def is_sellable(self, mint, probe_units=1_000_000):
        return self.sellable
    async def get_token_balance(self, mint):
        return self.balance
    async def sell(self, mint, amount, slippage):
        self.sold.append(mint); self.sold_qty.append(amount); return {"success": True, "tx_hash": "SELLTX"}
    async def buy(self, mint, amount_sol, slippage):
        self.bought.append(mint)
        return {"success": True, "price": 0.0001, "out_amount": 1_000_000, "tx_hash": "BUYTX"}

class FakeHelius:
    def __init__(self, freeze=None, mint_auth=None, liquidity=None):
        self.freeze=freeze; self.mint_auth=mint_auth; self.liquidity=liquidity
    async def get_mint_authorities(self, mint):
        return {"mint_authority": self.mint_auth, "freeze_authority": self.freeze}
    async def get_pool_liquidity_sol(self, mint, platform=None):
        return self.liquidity


def _cfg():
    return {"trading": {"slippage_bps": 1500, "buy_amount_sol": 0.1},
            "portfolio": {"stop_loss_pct": -50, "take_profit_pct": 200,
                          "min_liquidity_sol": 5, "max_price_impact_pct": 15, "poll_interval_sec": 5},
            "safety": {"require_sellable": True, "block_freeze_authority": True, "block_mint_authority": False}}

async def _noop_notify(msg, level="info"): return None


# ---------- PnL : le bug critique ----------
class TestRealizablePnL(unittest.IsolatedAsyncioTestCase):
    async def _run_check(self, value_sol, impact=0.0):
        fake = FakeJupiter(value_sol=value_sol, impact=impact)
        pm = PortfolioManager(_cfg(), fake, FakeHelius(), asyncio.Queue(), _noop_notify)
        pm.add_position({"token": "MINT", "amount_sol": 0.1, "amount_tokens": 1_000_000, "entry_price": 0.0001})
        await pm._check()
        return fake.sold

    async def test_breakeven_does_not_dump(self):
        # Le bug d'origine donnait ~-99.9% et déclenchait un stop-loss immédiat. Ici: PnL=0 -> on garde.
        self.assertEqual(await self._run_check(value_sol=0.1), [])

    async def test_stop_loss(self):
        self.assertEqual(await self._run_check(value_sol=0.04), ["MINT"])   # -60%

    async def test_take_profit(self):
        self.assertEqual(await self._run_check(value_sol=0.35), ["MINT"])   # +250%

    async def test_impact_exit(self):
        self.assertEqual(await self._run_check(value_sol=0.1, impact=20.0), ["MINT"])


# ---------- Vente sur solde RÉEL on-chain ----------
class TestRealBalanceSell(unittest.IsolatedAsyncioTestCase):
    def _pm(self, fake, queue=None):
        pm = PortfolioManager(_cfg(), fake, FakeHelius(), queue or asyncio.Queue(), _noop_notify)
        pm.add_position({"token": "MINT", "amount_sol": 0.1, "amount_tokens": 1_000_000, "entry_price": 0.0001})
        return pm

    async def test_sell_uses_real_balance_not_quote(self):
        fake = FakeJupiter(value_sol=0.04, balance=777)   # -60% -> stop-loss
        await self._pm(fake)._check()
        self.assertEqual(fake.sold, ["MINT"])
        self.assertEqual(fake.sold_qty, [777])            # solde réel, pas le 1_000_000 quoté

    async def test_zero_balance_closes_without_selling(self):
        fake = FakeJupiter(value_sol=0.04, balance=0); q = asyncio.Queue()
        pm = self._pm(fake, q)
        await pm._check()
        self.assertEqual(fake.sold, [])                   # aucune vente tentée
        self.assertNotIn("MINT", pm.positions)            # position retirée
        self.assertFalse(q.empty())                       # event position_closed émis

    async def test_fallback_to_quote_when_balance_none(self):
        fake = FakeJupiter(value_sol=0.04, balance=None)  # dry_run / RPC KO
        await self._pm(fake)._check()
        self.assertEqual(fake.sold_qty, [1_000_000])      # retombe sur le montant quoté


# ---------- Liquidité réelle (Pump.fun bonding curve) ----------
class TestPumpfunDecode(unittest.TestCase):
    def test_decode_and_liquidity(self):
        import struct
        from utils import pumpfun
        data = bytes(8) + struct.pack("<QQQQQ", 100, 30_000_000_000, 200, 12_000_000_000, 1_000_000) + b"\x00"
        dec = pumpfun.decode_bonding_curve(data)
        self.assertEqual(dec["real_sol_reserves"], 12_000_000_000)
        self.assertFalse(dec["complete"])
        self.assertAlmostEqual(pumpfun.bonding_curve_liquidity_sol(dec), 12.0)

    def test_decode_too_short(self):
        from utils import pumpfun
        self.assertIsNone(pumpfun.decode_bonding_curve(b"\x00" * 10))
        self.assertIsNone(pumpfun.decode_bonding_curve(None))

    def test_pda_deterministic(self):
        from utils import pumpfun
        m = "So11111111111111111111111111111111111111112"
        self.assertEqual(pumpfun.bonding_curve_pda(m), pumpfun.bonding_curve_pda(m))
        self.assertTrue(32 <= len(pumpfun.bonding_curve_pda(m)) <= 44)


class TestPumpfunWiring(unittest.IsolatedAsyncioTestCase):
    def _hc(self, real_sol, complete=False):
        import struct
        from utils.helius_client import HeliusClient
        hc = HeliusClient({"api_key": "x", "rest_url": "http://x", "rpc_url": "http://x"})
        data = bytes(8) + struct.pack("<QQQQQ", 1, 1, 1, real_sol, 1) + (b"\x01" if complete else b"\x00")
        async def fake_acc(addr): return data
        hc.get_account_info_b64 = fake_acc
        return hc

    async def test_reads_real_sol_reserves(self):
        hc = self._hc(7_500_000_000)
        liq = await hc.get_pumpfun_liquidity_sol("So11111111111111111111111111111111111111112")
        self.assertAlmostEqual(liq, 7.5)

    async def test_completed_curve_returns_none(self):
        hc = self._hc(7_500_000_000, complete=True)   # migré -> plus la source de liquidité
        self.assertIsNone(await hc.get_pumpfun_liquidity_sol("So11111111111111111111111111111111111111112"))


class TestLiquidityExit(unittest.IsolatedAsyncioTestCase):
    def _pm(self, liquidity):
        fake = FakeJupiter(value_sol=0.1)             # PnL ~0 : ni SL ni TP
        pm = PortfolioManager(_cfg(), fake, FakeHelius(liquidity=liquidity), asyncio.Queue(), _noop_notify)
        pm.add_position({"token": "MINT", "platform": "pump.fun", "amount_sol": 0.1, "amount_tokens": 1_000_000})
        return pm, fake

    async def test_low_liquidity_exits(self):
        pm, fake = self._pm(liquidity=2.0)            # < min_liquidity_sol (5)
        await pm._check()
        self.assertEqual(fake.sold, ["MINT"])

    async def test_healthy_liquidity_holds(self):
        pm, fake = self._pm(liquidity=50.0)
        await pm._check()
        self.assertEqual(fake.sold, [])


# ---------- Garde-fous sécurité ----------
class TestSafetyGate(unittest.IsolatedAsyncioTestCase):
    async def _snipe(self, helius, jupiter):
        sniper = TokenSniper(_cfg(), jupiter, helius, asyncio.Queue(), _noop_notify)
        await sniper.snipe({"mint": "MINT", "platform": "pump.fun"})
        return jupiter

    async def test_blocks_freeze_authority(self):
        j = await self._snipe(FakeHelius(freeze="SomeAuthority"), FakeJupiter())
        self.assertEqual(j.bought, [])

    async def test_blocks_non_sellable(self):
        j = await self._snipe(FakeHelius(), FakeJupiter(sellable=False))
        self.assertEqual(j.bought, [])

    async def test_allows_clean_token(self):
        j = await self._snipe(FakeHelius(), FakeJupiter())
        self.assertEqual(j.bought, ["MINT"])

    async def test_dedupe_no_double_buy(self):
        j = FakeJupiter(); sniper = TokenSniper(_cfg(), j, FakeHelius(), asyncio.Queue(), _noop_notify)
        await sniper.snipe({"mint": "MINT", "platform": "pump.fun"})
        await sniper.snipe({"mint": "MINT", "platform": "pump.fun"})
        self.assertEqual(j.bought, ["MINT"])


# ---------- Passphrase hors unité systemd ----------
class TestPassphrase(unittest.TestCase):
    def test_env_takes_priority(self):
        os.environ["SNIPER_KEY_PASSPHRASE"] = "from_env"
        try: self.assertEqual(get_passphrase(), "from_env")
        finally: os.environ.pop("SNIPER_KEY_PASSPHRASE", None)

    def test_file_fallback(self):
        os.environ.pop("SNIPER_KEY_PASSPHRASE", None)
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("  from_file\n"); path = f.name
        os.environ["SNIPER_KEY_PASSPHRASE_FILE"] = path
        try: self.assertEqual(get_passphrase(), "from_file")
        finally:
            os.environ.pop("SNIPER_KEY_PASSPHRASE_FILE", None); os.unlink(path)


# ---------- Crypto round-trip ----------
class TestCrypto(unittest.TestCase):
    def test_roundtrip(self):
        raw = bytes(range(64)); pk_b58 = base58.b58encode(raw).decode()
        token = encrypt_private_key(pk_b58, "pw")
        self.assertEqual(decrypt_private_key(token, "pw"), raw)


# ---------- Détecteurs (couverture que le README revendiquait) ----------
class TestDetectors(unittest.TestCase):
    def test_vanity(self):
        self.assertEqual(D.detect_vanity("pumpABCdef", ["pump", "moon"]), "pump")
        self.assertIsNone(D.detect_vanity("XyzABC", ["pump"]))

    def test_token_creation_pumpfun(self):
        tx = {"instructions": [{"programId": D.PUMP_FUN}], "tokenTransfers": [{"mint": "MINTXYZ"}]}
        out = D.detect_token_creation(tx)
        self.assertEqual(out["mint"], "MINTXYZ"); self.assertEqual(out["platform"], "pump.fun")

    def test_convergence_wallet(self):
        g = WalletGraph(window_sec=600, dust_threshold=0.05)
        for src in ("A", "B", "C"): g.add_edge(src, "DST", 1.0, "sig")
        self.assertEqual(g.find_convergence_wallet(min_in=3), "DST")


# ---------- get_price : même unité que entry_price (lamports / unité) ----------
class TestJupiterUnits(unittest.IsolatedAsyncioTestCase):
    async def test_get_price_and_value_units(self):
        try:
            from utils.jupiter_client import JupiterClient, LAMPORTS
        except Exception as e:
            self.skipTest(f"solders/tenacity indisponibles: {e}")
        jc = JupiterClient({"quote_url": "x", "swap_url": "x", "sol_mint": "SOL"},
                           {"priority_fee_lamports": 0}, {"encrypted_private_key": ""}, dry_run=True)

        async def fake_quote(inp, out, amount, slip):
            return {"outAmount": str(int(amount) * 7), "priceImpactPct": "0.02"}
        jc.get_quote = fake_quote
        # 7 lamports de SOL par unité de token, peu importe le probe
        self.assertAlmostEqual(await jc.get_price("MINT", probe_units=1_000_000), 7.0, places=6)
        val = await jc.get_position_value("MINT", 2 * LAMPORTS)
        self.assertAlmostEqual(val["value_sol"], 2 * 7, places=6)   # outAmount=2e9*7 lamports -> /1e9
        self.assertAlmostEqual(val["price_impact_pct"], 2.0, places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
