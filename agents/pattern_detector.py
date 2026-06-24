"""Agent 2 - Détection patterns : direct, brouillage, bundling, LP timing, vanity."""
import asyncio, time
from utils.wallet_graph import WalletGraph
from utils import detectors as D
from utils.logger import get_logger
log = get_logger("pattern_detector")

class PatternDetector:
    def __init__(self, config, helius, event_queue):
        self.helius=helius; self.queue=event_queue
        self.window=config["funding"]["graph_window_minutes"]*60
        self.dust=config["funding"]["dust_threshold_sol"]
        self.pcfg=config["patterns"]
        self.vanity=[p.lower() for p in config["patterns"].get("vanity_prefixes",[])]
        self.tracked={}; self.graph=WalletGraph(window_sec=self.window, dust_threshold=self.dust)
    async def track_wallet(self, wallet, origin_sig):
        self.tracked[wallet]={"start":time.time(),"origin_sig":origin_sig,"depth":0}
        self.graph.add_node(wallet, role="seed")
        log.info("tracking_started", extra={"wallet":wallet})
        if self.pcfg.get("detect_vanity"):
            v=D.detect_vanity(wallet, self.vanity)
            if v: log.info("vanity_wallet", extra={"wallet":wallet,"prefix":v})
    async def run(self):
        while True:
            try:
                await self._poll(); self.graph.prune_expired()
            except asyncio.CancelledError: raise
            except Exception as e: log.exception("pattern_poll_error", extra={"error":str(e)})
            await asyncio.sleep(3)
    async def _poll(self):
        now=time.time()
        for w,m in list(self.tracked.items()):
            if now-m["start"]>self.window: self.tracked.pop(w,None); continue
            await self._inspect(w,m)
    async def _inspect(self, wallet, meta):
        for si in await self.helius.get_signatures(wallet, limit=25):
            sig=si["signature"]; tx=await self.helius.get_transaction(sig)
            if not tx: continue
            created=self._creation(tx)
            if created: await self._emit(wallet, created, "direct", sig); continue
            for tr in self.helius.extract_native_transfers(tx):
                if tr["fromUserAccount"]==wallet:
                    self.graph.add_edge(wallet, tr["toUserAccount"], tr["amount"]/1e9, sig)
                    dst=tr["toUserAccount"]
                    if dst not in self.tracked and meta["depth"]<3:
                        self.tracked[dst]={"start":time.time(),"origin_sig":sig,"depth":meta["depth"]+1}
        conv=self.graph.find_convergence_wallet()
        if conv and conv not in self.tracked:
            log.info("convergence_detected", extra={"wallet":conv})
            self.tracked[conv]={"start":time.time(),"origin_sig":meta["origin_sig"],"depth":0}
    def _creation(self, tx):
        ixs=[{"programId":i.get("programId"),"parsed":i.get("parsed",{})} for i in self.helius.extract_instructions(tx)]
        return D.detect_token_creation({"instructions":ixs,"tokenTransfers":tx.get("tokenTransfers",[]),
                                        "accountData":tx.get("accountData",[])})
    async def _emit(self, creator, created, pattern, sig):
        if not created.get("mint"): return
        bundling=lp=False
        if self.pcfg.get("detect_bundling"): bundling=await self.helius.detect_bundling(created["mint"], sig)
        if self.pcfg.get("detect_lp_timing"): lp=await self.helius.detect_lp_timing(creator, sig)
        log.info("token_creation", extra={"creator":creator,"mint":created["mint"],
            "platform":created["platform"],"pattern":pattern,"bundling":bundling,"lp_timing":lp})
        await self.queue.put({"type":"token_creation_detected","data":{
            "creator":creator,"mint":created["mint"],"platform":created["platform"],
            "pattern":pattern,"signature":sig,"flags":{"bundling":bundling,"lp_timing":lp}}})
        self.tracked.pop(creator, None)
