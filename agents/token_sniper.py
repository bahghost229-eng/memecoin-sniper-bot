"""Agent 3 - Achat immédiat via Jupiter dès détection, avec garde-fous de sécurité."""
import asyncio, time
from utils.safety import check_token
from utils.logger import get_logger
log = get_logger("token_sniper")

class TokenSniper:
    def __init__(self, config, jupiter, helius, event_queue, notify):
        self.jupiter=jupiter; self.helius=helius; self.queue=event_queue; self.notify=notify
        self.buy_amount_sol=config["trading"]["buy_amount_sol"]
        self.slippage_bps=config["trading"]["slippage_bps"]; self._lock=asyncio.Lock()
        sc=config.get("safety", {})
        self.require_sellable=sc.get("require_sellable", True)
        self.block_freeze_authority=sc.get("block_freeze_authority", True)
        self.block_mint_authority=sc.get("block_mint_authority", False)
        self._seen=set()                       # anti double-achat du même mint
    async def run(self):
        while True: await asyncio.sleep(3600)
    async def _safety_check(self, mint):
        """Garde-fous anti-rug/honeypot AVANT tout achat (logique partagée)."""
        return await check_token(self.helius, self.jupiter, mint,
            require_sellable=self.require_sellable, block_freeze=self.block_freeze_authority,
            block_mint=self.block_mint_authority)
    async def snipe(self, data):
        mint=data["mint"]
        async with self._lock:
            if mint in self._seen:
                log.info("snipe_skipped_duplicate", extra={"mint":mint}); return
            try:
                ok, reason = await self._safety_check(mint)
                if not ok:
                    log.info("snipe_blocked_safety", extra={"mint":mint,"reason":reason})
                    await self.notify(f"\U0001F6E1️ Snipe bloqué (sécurité)\n`{mint}`\n{reason}","info"); return
                self._seen.add(mint)
                log.info("snipe_start", extra={"mint":mint,"amount_sol":self.buy_amount_sol})
                res=await self.jupiter.buy(mint, self.buy_amount_sol, self.slippage_bps)
                if not res["success"]:
                    self._seen.discard(mint)
                    await self.notify(f"⚠️ Snipe échoué {mint}: {res.get('error')}","critical"); return
                pos={"token":mint,"platform":data.get("platform"),"entry_price":res["price"],
                     "amount_sol":self.buy_amount_sol,"amount_tokens":res["out_amount"],
                     "tx_hash":res["tx_hash"],"opened_at":time.time()}
                log.info("snipe_success", extra=pos)
                await self.notify(f"✅ Achat\nToken: `{mint}`\nPrix: {res['price']:.10f}\nTx: `{res['tx_hash']}`","info")
                await self.queue.put({"type":"position_opened","data":pos})
            except Exception as e:
                self._seen.discard(mint)
                log.exception("snipe_error", extra={"mint":mint,"error":str(e)})
                await self.notify(f"❌ Erreur snipe {mint}: {e}","critical")
