"""Trading manuel (style terminal Telegram) : infos token, achat, vente par %.

Réutilise JupiterClient (quote/buy/sell/balance/value), la lecture de liquidité
on-chain et les garde-fous anti-honeypot partagés.
"""
import time
from utils.safety import check_token
from utils.logger import get_logger
log = get_logger("manual_trader")
LAMPORTS = 1_000_000_000


class ManualTrader:
    def __init__(self, config, jupiter, helius, portfolio, event_queue=None):
        self.cfg = config; self.jupiter = jupiter; self.helius = helius
        self.portfolio = portfolio; self.queue = event_queue
        self.slippage = config["trading"]["slippage_bps"]
        sc = config.get("safety", {})
        self.require_sellable = sc.get("require_sellable", True)
        self.block_freeze = sc.get("block_freeze_authority", True)
        self.block_mint = sc.get("block_mint_authority", False)

    async def token_info(self, mint):
        """Agrège ce qu'il faut pour décider d'un achat."""
        info = {"mint": mint}
        auth = await self.helius.get_mint_authorities(mint) or {}
        info["freeze_authority"] = bool(auth.get("freeze_authority"))
        info["mint_authority"] = bool(auth.get("mint_authority"))
        info["sellable"] = await self.jupiter.is_sellable(mint)
        info["liquidity_sol"] = await self.helius.get_pool_liquidity_sol(mint, None)
        info["price_lamports_per_unit"] = await self.jupiter.get_price(mint)
        return info

    async def buy(self, mint, amount_sol):
        ok, reason = await check_token(
            self.helius, self.jupiter, mint, require_sellable=self.require_sellable,
            block_freeze=self.block_freeze, block_mint=self.block_mint)
        if not ok:
            log.info("manual_buy_blocked", extra={"mint": mint, "reason": reason})
            return {"success": False, "blocked": True, "error": reason}
        res = await self.jupiter.buy(mint, amount_sol, self.slippage)
        if res.get("success"):
            pos = {"token": mint, "platform": "manual", "entry_price": res.get("price"),
                   "amount_sol": amount_sol, "amount_tokens": res.get("out_amount"),
                   "tx_hash": res.get("tx_hash"), "opened_at": time.time()}
            self.portfolio.add_position(pos)
            log.info("manual_buy_ok", extra={"mint": mint, "amount_sol": amount_sol})
        return res

    async def sell(self, mint, pct=100):
        if pct <= 0 or pct > 100:
            return {"success": False, "error": "pourcentage invalide"}
        bal = await self.jupiter.get_token_balance(mint)
        if bal is None:   # dry_run / RPC KO -> on retombe sur la quantité de la position
            bal = (self.portfolio.positions.get(mint) or {}).get("amount_tokens", 0)
        if not bal or bal <= 0:
            self.portfolio.remove_position(mint)
            return {"success": False, "error": "solde nul"}
        sell_qty = int(bal * pct / 100)
        if sell_qty <= 0:
            return {"success": False, "error": "montant trop petit"}
        res = await self.jupiter.sell(mint, sell_qty, self.slippage)
        if res.get("success") and pct >= 100:
            self.portfolio.remove_position(mint)
        return res
