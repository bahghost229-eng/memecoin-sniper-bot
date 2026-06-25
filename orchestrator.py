"""Orchestrateur : relie les 5 agents via une file d'événements asynchrone."""
import asyncio
from agents.funding_monitor import FundingWalletMonitor
from agents.pattern_detector import PatternDetector
from agents.token_sniper import TokenSniper
from agents.portfolio_manager import PortfolioManager
from agents.manual_trader import ManualTrader
from agents.telegram_bot import TelegramInterface
from utils.helius_client import HeliusClient
from utils.jupiter_client import JupiterClient
from utils.logger import get_logger
log = get_logger("orchestrator")

class Orchestrator:
    def __init__(self, config):
        self.config=config; self.state={"paused":config["general"].get("paused",False)}
        self.event_queue=asyncio.Queue()
        self.helius=HeliusClient(config["helius"])
        self.jupiter=JupiterClient(config["jupiter"],config["trading"],config["wallet"],dry_run=config["general"]["dry_run"])
        self.telegram=TelegramInterface(config, self)
        self.funding_monitor=FundingWalletMonitor(config, self.helius, self.event_queue)
        self.pattern_detector=PatternDetector(config, self.helius, self.event_queue)
        self.token_sniper=TokenSniper(config, self.jupiter, self.helius, self.event_queue, self.notify)
        self.portfolio=PortfolioManager(config, self.jupiter, self.helius, self.event_queue, self.notify)
        self.trader=ManualTrader(config, self.jupiter, self.helius, self.portfolio, self.event_queue)
        self._tasks=[]
    async def notify(self, message, level="info"): await self.telegram.send_notification(message, level)
    def is_paused(self): return self.state["paused"]
    def set_paused(self, p): self.state["paused"]=p; log.info("pause_state_changed", extra={"paused":p})
    async def start(self):
        log.info("orchestrator_starting")
        self._tasks=[asyncio.create_task(c, name=n) for c,n in [
            (self.funding_monitor.run(),"funding_monitor"),(self.pattern_detector.run(),"pattern_detector"),
            (self.token_sniper.run(),"token_sniper"),(self.portfolio.run(),"portfolio_manager"),
            (self.telegram.run(),"telegram"),(self._dispatch(),"dispatch")]]
        await self.notify("🚀 Sniper bot démarré","info")
    async def _dispatch(self):
        while True:
            ev=await self.event_queue.get()
            try: await self._route(ev)
            except Exception as e:
                log.exception("dispatch_error", extra={"error":str(e)})
                await self.notify(f"❌ Erreur dispatch: {e}","critical")
            finally: self.event_queue.task_done()
    async def _route(self, ev):
        t=ev["type"]; log.info("event", extra={"event_type":t,"data":ev.get("data")})
        if t=="fresh_wallet_funded":
            d=ev["data"]
            await self.notify(f"🆕 Fresh wallet financé\n{d['wallet']}\n{d['amount_sol']} SOL","info")
            await self.pattern_detector.track_wallet(d["wallet"], d["signature"])
        elif t=="token_creation_detected":
            if self.is_paused(): await self.notify("⏸️ Création détectée mais PAUSE","info"); return
            await self.token_sniper.snipe(ev["data"])
        elif t=="position_opened": self.portfolio.add_position(ev["data"])
        elif t=="position_closed": self.portfolio.remove_position(ev["data"]["token"])
    async def stop(self):
        log.info("orchestrator_stopping"); await self.notify("🛑 Sniper bot arrêté","info")
        for t in self._tasks: t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.helius.close(); await self.jupiter.close()
