"""Agent 4 - Gestion des positions : stop-loss, take-profit, impact/liquidité.

PnL calculé sur la VALEUR RÉALISABLE (revente simulée -> SOL) vs le coût en SOL,
ce qui évite tout mélange d'unités entre prix d'entrée et prix courant.
"""
import asyncio
from utils.logger import get_logger
log = get_logger("portfolio_manager")

class PortfolioManager:
    def __init__(self, config, jupiter, helius, event_queue, notify):
        self.jupiter=jupiter; self.helius=helius; self.queue=event_queue; self.notify=notify; self.cfg=config
        self.stop_loss=config["portfolio"]["stop_loss_pct"]; self.take_profit=config["portfolio"]["take_profit_pct"]
        self.min_liquidity=config["portfolio"].get("min_liquidity_sol", 0)
        self.max_impact=config["portfolio"].get("max_price_impact_pct", 0)   # 0 = désactivé
        self.poll=config["portfolio"]["poll_interval_sec"]
        self.positions={}
    def add_position(self, p): self.positions[p["token"]]=p; log.info("position_added", extra={"token":p["token"]})
    def remove_position(self, t): self.positions.pop(t, None)
    def list_positions(self): return list(self.positions.values())
    async def run(self):
        while True:
            try: await self._check()
            except asyncio.CancelledError: raise
            except Exception as e: log.exception("portfolio_error", extra={"error":str(e)})
            await asyncio.sleep(self.poll)
    async def _live_qty(self, token, pos):
        """Solde RÉEL on-chain si dispo, sinon le montant quoté à l'achat (fallback dry_run / RPC KO)."""
        bal = await self.jupiter.get_token_balance(token)
        return pos.get("amount_tokens", 0) if bal is None else bal
    async def _check(self):
        for token,pos in list(self.positions.items()):
            try:
                qty = await self._live_qty(token, pos)
                if qty is not None and qty <= 0:        # plus de tokens : vendu hors-bot / transfert / rug
                    log.info("position_emptied", extra={"token":token})
                    self.remove_position(token)
                    await self.notify(f"ℹ️ Position vidée on-chain (vendue/transférée/rug) : {token}","info")
                    await self.queue.put({"type":"position_closed","data":{"token":token}})
                    continue
                val=await self.jupiter.get_position_value(token, qty)
                if val is None: continue
                cost=pos.get("amount_sol",0) or 0
                value_sol=val["value_sol"]; impact=val.get("price_impact_pct",0)
                pnl=((value_sol-cost)/cost)*100 if cost else 0
                reason=None
                if pnl<=self.stop_loss: reason=f"STOP-LOSS ({pnl:.1f}%)"
                elif pnl>=self.take_profit: reason=f"TAKE-PROFIT (+{pnl:.1f}%)"
                elif self.max_impact and impact>=self.max_impact: reason=f"LIQUIDITÉ FAIBLE (impact {impact:.1f}%)"
                if reason: await self._sell(token,pos,pnl,reason,qty)
            except Exception as e: log.warning("position_check_failed", extra={"token":token,"error":str(e)})
    async def _sell(self, token, pos, pnl, reason, qty):
        log.info("sell_triggered", extra={"token":token,"reason":reason,"pnl":pnl,"qty":qty})
        try:
            res=await self.jupiter.sell(token, qty, self.cfg["trading"]["slippage_bps"])
            if not res["success"]:
                await self.notify(f"⚠️ Vente échouée {token}: {res.get('error')}","critical"); return
            await self.notify(f"💰 Vente ({reason})\nToken: {token}\nPnL: {pnl:+.1f}%\nTx: {res.get('tx_hash')}","info")
            await self.queue.put({"type":"position_closed","data":{"token":token}})
        except Exception as e:
            log.exception("sell_error", extra={"token":token,"error":str(e)})
            await self.notify(f"❌ Erreur vente {token}: {e}","critical")
