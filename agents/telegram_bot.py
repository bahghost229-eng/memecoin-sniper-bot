"""Agent 5 - Interface Telegram (commandes + notifications)."""
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes
from utils.logger import get_logger
log = get_logger("telegram")

class TelegramInterface:
    def __init__(self, config, orchestrator):
        self.cfg=config; self.orch=orchestrator
        self.token=config["telegram"]["bot_token"]; self.chat_id=config["telegram"]["chat_id"]; self.app=None
    async def run(self):
        self.app=Application.builder().token(self.token).build()
        for cmd,fn in [("status",self.status),("wallets",self.wallets),("add_wallet",self.add_wallet),
                       ("positions",self.positions),("config",self.config),("pause",self.pause),("resume",self.resume)]:
            self.app.add_handler(CommandHandler(cmd, fn))
        await self.app.initialize(); await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        log.info("telegram_started")
    async def send_notification(self, message, level="info"):
        if not self.app: return
        prefix={"info":"","critical":"🚨 "}.get(level,"")
        try: await self.app.bot.send_message(chat_id=self.chat_id, text=prefix+message, parse_mode=ParseMode.MARKDOWN)
        except Exception as e: log.warning("notify_failed", extra={"error":str(e)})
    def _auth(self, u): return str(u.effective_chat.id)==str(self.chat_id)
    async def status(self, u, c):
        if not self._auth(u): return
        msg=(f"*Sniper Bot*\nÉtat: {'⏸️ PAUSE' if self.orch.is_paused() else '▶️ ACTIF'}\n"
             f"Mode: {'🧪 DRY-RUN' if self.cfg['general']['dry_run'] else '🔴 LIVE'}\n"
             f"Wallets: {len(self.orch.funding_monitor.funding_wallets)}\n"
             f"Tracés: {len(self.orch.pattern_detector.tracked)}\n"
             f"Positions: {len(self.orch.portfolio.list_positions())}")
        await u.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    async def wallets(self, u, c):
        if not self._auth(u): return
        ws=self.orch.funding_monitor.funding_wallets
        await u.message.reply_text("*Wallets surveillés:*\n"+"\n".join(f"`{w}`" for w in ws) or "Aucun", parse_mode=ParseMode.MARKDOWN)
    async def add_wallet(self, u, c):
        if not self._auth(u): return
        if not c.args: await u.message.reply_text("Usage: /add_wallet <address>"); return
        self.orch.funding_monitor.add_funding_wallet(c.args[0])
        await u.message.reply_text(f"✅ Ajouté: {c.args[0]} (redémarrage requis pour WS)")
    async def positions(self, u, c):
        if not self._auth(u): return
        ps=self.orch.portfolio.list_positions()
        if not ps: await u.message.reply_text("Aucune position"); return
        await u.message.reply_text("*Positions:*\n"+"\n".join(
            f"`{p['token']}` entrée {p['entry_price']:.10f} {p['amount_sol']} SOL" for p in ps), parse_mode=ParseMode.MARKDOWN)
    async def config(self, u, c):
        if not self._auth(u): return
        if not c.args:
            t=self.cfg["trading"]; pf=self.cfg["portfolio"]
            await u.message.reply_text(f"*Config*\nbuy_amount_sol: {t['buy_amount_sol']}\nslippage_bps: {t['slippage_bps']}\n"
                f"stop_loss_pct: {pf['stop_loss_pct']}\ntake_profit_pct: {pf['take_profit_pct']}\nmin_liquidity_sol: {pf['min_liquidity_sol']}",
                parse_mode=ParseMode.MARKDOWN); return
        if len(c.args)==2:
            ok=self._set(c.args[0], c.args[1])
            await u.message.reply_text(f"✅ {c.args[0]}={c.args[1]}" if ok else f"❌ Clé inconnue: {c.args[0]}")
    def _set(self, key, val):
        m={"buy_amount_sol":("trading",float),"slippage_bps":("trading",int),"stop_loss_pct":("portfolio",float),
           "take_profit_pct":("portfolio",float),"min_liquidity_sol":("portfolio",float)}
        if key not in m: return False
        sec,cast=m[key]; self.cfg[sec][key]=cast(val)
        if sec=="trading":
            self.orch.token_sniper.buy_amount_sol=self.cfg["trading"]["buy_amount_sol"]
            self.orch.token_sniper.slippage_bps=self.cfg["trading"]["slippage_bps"]
        if sec=="portfolio":
            self.orch.portfolio.stop_loss=self.cfg["portfolio"]["stop_loss_pct"]
            self.orch.portfolio.take_profit=self.cfg["portfolio"]["take_profit_pct"]
            self.orch.portfolio.min_liquidity=self.cfg["portfolio"]["min_liquidity_sol"]
        return True
    async def pause(self, u, c):
        if not self._auth(u): return
        self.orch.set_paused(True); await u.message.reply_text("⏸️ Pause")
    async def resume(self, u, c):
        if not self._auth(u): return
        self.orch.set_paused(False); await u.message.reply_text("▶️ Relancé")