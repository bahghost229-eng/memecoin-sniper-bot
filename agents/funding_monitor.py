"""Agent 1 - Surveillance des wallets financeurs via WebSocket Solana."""
import asyncio, json
import websockets
from utils.logger import get_logger
log = get_logger("funding_monitor")
LAMPORTS = 1_000_000_000

class FundingWalletMonitor:
    def __init__(self, config, helius, event_queue):
        self.ws_url=config["rpc"]["ws_url"]
        self.reconnect=config["rpc"]["ws_reconnect_delay"]; self.max_reconnect=config["rpc"]["ws_max_reconnect_delay"]
        self.funding_wallets=set(config["funding_wallets"])
        self.wallet_patterns={}   # addr -> "direct" | "obfuscation"
        self.min_sol=config["funding"]["min_sol"]; self.max_sol=config["funding"]["max_sol"]
        self.helius=helius; self.queue=event_queue
        self.ws=None; self._sub_id=1000
    def add_funding_wallet(self, addr, pattern=None):
        self.funding_wallets.add(addr)
        if pattern: self.wallet_patterns[addr]=pattern
        log.info("funding_wallet_added", extra={"wallet":addr,"pattern":pattern})
        if self.ws is not None:
            try: asyncio.get_event_loop().create_task(self._subscribe(addr))
            except Exception as e: log.warning("live_subscribe_failed", extra={"error":str(e)})
    async def _subscribe(self, w):
        if self.ws is None: return
        self._sub_id+=1
        await self.ws.send(json.dumps({"jsonrpc":"2.0","id":self._sub_id,"method":"logsSubscribe",
            "params":[{"mentions":[w]},{"commitment":"confirmed"}]}))
        log.info("ws_subscribed_live", extra={"wallet":w})
    async def run(self):
        delay=self.reconnect
        while True:
            try:
                await self._listen(); delay=self.reconnect
            except asyncio.CancelledError: raise
            except Exception as e:
                log.warning("ws_disconnected", extra={"error":str(e),"retry_in":delay})
                self.ws=None
                await asyncio.sleep(delay); delay=min(delay*2, self.max_reconnect)
    async def _listen(self):
        async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20) as ws:
            self.ws=ws
            log.info("ws_connected", extra={"url":self.ws_url})
            for i,w in enumerate(self.funding_wallets):
                await ws.send(json.dumps({"jsonrpc":"2.0","id":i+1,"method":"logsSubscribe",
                    "params":[{"mentions":[w]},{"commitment":"confirmed"}]}))
            async for raw in ws: await self._handle(json.loads(raw))
    async def _handle(self, msg):
        if "result" in msg and isinstance(msg["result"], int): return
        val = msg.get("params",{}).get("result",{}).get("value",{})
        sig = val.get("signature")
        if not sig or val.get("err"): return
        await self._analyze(sig)
    async def _analyze(self, sig):
        tx = await self.helius.get_transaction(sig)
        if not tx: return
        for tr in self.helius.extract_native_transfers(tx):
            src, dst = tr["fromUserAccount"], tr["toUserAccount"]
            amt = tr["amount"]/LAMPORTS
            if src not in self.funding_wallets: continue
            if not (self.min_sol <= amt <= self.max_sol): continue
            if not await self.helius.is_fresh_wallet(dst, exclude_sig=sig): continue
            log.info("fresh_wallet_funded", extra={"wallet":dst,"amount_sol":amt,"source":src,"sig":sig})
            await self.queue.put({"type":"fresh_wallet_funded","data":{
                "wallet":dst,"source":src,"amount_sol":round(amt,4),"signature":sig,
                "pattern_hint":self.wallet_patterns.get(src)}})
