"""Agent 3 - Achat immédiat via Jupiter dès détection de création."""
import asyncio, time
from utils.logger import get_logger
log = get_logger("token_sniper")

class TokenSniper:
    def __init__(self, config, jupiter, event_queue, notify):
        self.jupiter=jupiter; self.queue=event_queue; self.notify=notify
        self.buy_amount_sol=config["trading"]["buy_amount_sol"]
        self.slippage_bps=config["trading"]["slippage_bps"]; self._lock=asyncio.Lock()
    async def run(self):
        while True: await asyncio.sleep(3600)
    async def snipe(self, data):
        mint=data["mint"]
        async with self._lock:
            try:
                log.info("snipe_start", extra={"mint":mint,"amount_sol":self.buy_amount_sol})
                res=await self.jupiter.buy(mint, self.buy_amount_sol, self.slippage_bps)
                if not res["success"]:
                    await self.notify(f"⚠️ Snipe échoué {mint}: {res.get('error')}","critical"); return
                pos={"token":mint,"platform":data.get("platform"),"entry_price":res["price"],
                     "amount_sol":self.buy_amount_sol,"amount_tokens":res["out_amount"],
                     "tx_hash":res["tx_hash"],"opened_at":time.time()}
                log.info("snipe_success", extra=pos)
                await self.notify(f"✅ Achat\nToken: \`{mint}\`\nPrix: {res['price']:.10f}\nTx: \`{res['tx_hash']}\`","info")
                await self.queue.put({"type":"position_opened","data":pos})
            except Exception as e:
                log.exception("snipe_error", extra={"mint":mint,"error":str(e)})
                await self.notify(f"❌ Erreur snipe {mint}: {e}","critical")
