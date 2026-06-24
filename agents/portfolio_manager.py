"""Agent 4 - Gestion des positions : stop-loss, take-profit, liquidité."""
import asyncio
from utils.logger import get_logger
log = get_logger("portfolio_manager")

class PortfolioManager:
    def __init__(self, config, jupiter, helius, event_queue, notify):
        self.jupiter=jupiter; self.helius=helius; self.queue=event_queue; self.notify=notify; self.cfg=config
        self.stop_loss=config["portfolio"]["stop_loss_pct"]; self.take_profit=config["portfolio"]["take_profit_pct"]
        self.min_liquidity=config["portfolio"]["min_liquidity_sol"]; self.poll=config["portfolio"]["poll_interval_sec"]
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
    async def _check(self):
        for token,pos in list(self.positions.items()):
            try:
                price=await self.jupiter.get_price(token)
                if price is None: continue
                entry=pos["entry_price"]; pnl=((price-entry)/entry)*100 if entry else 0
                liq=await self.helius.get_pool_liquidity_sol(token)
                reason=None
                if pnl<=self.stop_loss: reason=f"STOP-LOSS ({pnl:.1f}%)"
                elif pnl>=self.take_profit: reason=f"TAKE-PROFIT (+{pnl:.1f}%)"
                elif liq is not None and liq<self.min_liquidity: reason=f"LIQUIDITÉ FAIBLE ({liq:.2f} SOL)"
                if reason: await self._sell(token,pos,pnl,reason)
            except Exception as e: log.warning("position_check_failed", extra={"token":token,"error":str(e)})
    async def _sell(self, token, pos, pnl, reason):
        log.info("sell_triggered", extra={"token":token,"reason":reason,"pnl":pnl})
        try:
            res=await self.jupiter.sell(token, pos["amount_tokens"], self.cfg["trading"]["slippage_bps"])
            if not res["success"]:
                await self.notify(f"⚠️ Vente échouée {token}: {res.get('error')}","critical"); return
            await self.notify(f"💰 Vente ({reason})\nToken: {token}\nPnL: {pnl:+.1f}%\nTx: {res['tx_hash']}","info")
            await self.queue.put({"type":"position_closed","data":{"token":token}})
        except Exception as e:
            log.exception("sell_error", extra={"token":token,"error":str(e)})
            await self.notify(f"❌ Erreur vente {token}: {e}","critical")
