"""Agent 5 - Interface Telegram (commandes + notifications + boutons)."""
from telegram import (Update, BotCommand, ReplyKeyboardMarkup, KeyboardButton,
                      InlineKeyboardMarkup, InlineKeyboardButton)
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from utils.logger import get_logger
log = get_logger("telegram")

BOT_COMMANDS = [
    BotCommand("menu", "Afficher le clavier de boutons"),
    BotCommand("status", "Etat du bot"),
    BotCommand("wallets", "Wallets surveilles"),
    BotCommand("add_wallet", "Ajouter un wallet financeur"),
    BotCommand("positions", "Positions ouvertes"),
    BotCommand("config", "Voir/modifier la config"),
    BotCommand("pause", "Mettre en pause"),
    BotCommand("resume", "Reprendre"),
]

REPLY_KB = ReplyKeyboardMarkup(
    [["/status", "/positions"], ["/wallets", "/config"], ["/pause", "/resume"]],
    resize_keyboard=True)

def inline_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001F4CA Status", callback_data="status"),
         InlineKeyboardButton("\U0001F4BC Positions", callback_data="positions")],
        [InlineKeyboardButton("\U0001F45B Wallets", callback_data="wallets"),
         InlineKeyboardButton("\u2699\uFE0F Config", callback_data="config")],
        [InlineKeyboardButton("\u23F8\uFE0F Pause", callback_data="pause"),
         InlineKeyboardButton("\u25B6\uFE0F Resume", callback_data="resume")],
    ])

class TelegramInterface:
    def __init__(self, config, orchestrator):
        self.cfg=config; self.orch=orchestrator
        self.token=config["telegram"]["bot_token"]; self.chat_id=config["telegram"]["chat_id"]; self.app=None
    async def run(self):
        self.app=Application.builder().token(self.token).build()
        for cmd,fn in [("start",self.menu),("menu",self.menu),("status",self.status),("wallets",self.wallets),
                       ("add_wallet",self.add_wallet),("positions",self.positions),("config",self.config),
                       ("pause",self.pause),("resume",self.resume)]:
            self.app.add_handler(CommandHandler(cmd, fn))
        self.app.add_handler(CallbackQueryHandler(self.on_button))
        await self.app.initialize(); await self.app.start()
        try: await self.app.bot.set_my_commands(BOT_COMMANDS)
        except Exception as e: log.warning("set_commands_failed", extra={"error":str(e)})
        await self.app.updater.start_polling(drop_pending_updates=True)
        log.info("telegram_started")
    async def send_notification(self, message, level="info"):
        if not self.app: return
        prefix={"info":"","critical":"\U0001F6A8 "}.get(level,"")
        try: await self.app.bot.send_message(chat_id=self.chat_id, text=prefix+message, parse_mode=ParseMode.MARKDOWN)
        except Exception as e: log.warning("notify_failed", extra={"error":str(e)})
    def _auth(self, u): return str(u.effective_chat.id)==str(self.chat_id)
    async def _reply(self, u, text, kb=None):
        await u.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
    async def menu(self, u, c):
        if not self._auth(u): return
        await self._reply(u, "*\U0001F3AF Sniper Bot - Menu*\nUtilise les boutons ci-dessous :", inline_kb())
        await self._reply(u, "Clavier rapide active \U0001F447", REPLY_KB)
    async def on_button(self, u, c):
        if not self._auth(u): return
        q=u.callback_query; await q.answer()
        fn={"status":self.status,"wallets":self.wallets,"positions":self.positions,
            "config":self.config,"pause":self.pause,"resume":self.resume}.get(q.data)
        if fn: await fn(u, c)
    async def status(self, u, c):
        if not self._auth(u): return
        etat = "\u23F8\uFE0F PAUSE" if self.orch.is_paused() else "\u25B6\uFE0F ACTIF"
        mode = "\U0001F9EA DRY-RUN" if self.cfg["general"]["dry_run"] else "\U0001F534 LIVE"
        nw = len(self.orch.funding_monitor.funding_wallets)
        nt = len(self.orch.pattern_detector.tracked)
        npos = len(self.orch.portfolio.list_positions())
        msg = ("*Sniper Bot*\n" + "Etat: " + etat + "\n" + "Mode: " + mode + "\n"
               + "Wallets: " + str(nw) + "\n" + "Traces: " + str(nt) + "\n" + "Positions: " + str(npos))
        await self._reply(u, msg)
    async def wallets(self, u, c):
        if not self._auth(u): return
        ws=self.orch.funding_monitor.funding_wallets
        await self._reply(u, "*Wallets surveilles:*\n"+("\n".join(f"`{w}`" for w in ws) or "Aucun"))
    async def add_wallet(self, u, c):
        if not self._auth(u): return
        if not c.args: await self._reply(u, "Usage: /add_wallet <address>"); return
        self.orch.funding_monitor.add_funding_wallet(c.args[0])
        await self._reply(u, f"\u2705 Ajoute: {c.args[0]} (redemarrage requis pour WS)")
    async def positions(self, u, c):
        if not self._auth(u): return
        ps=self.orch.portfolio.list_positions()
        if not ps: await self._reply(u, "Aucune position"); return
        await self._reply(u, "*Positions:*\n"+"\n".join(
            f"`{p['token']}` entree {p['entry_price']:.10f} {p['amount_sol']} SOL" for p in ps))
    async def config(self, u, c):
        if not self._auth(u): return
        if not getattr(c,"args",None):
            t=self.cfg["trading"]; pf=self.cfg["portfolio"]
            await self._reply(u, f"*Config*\nbuy_amount_sol: {t['buy_amount_sol']}\nslippage_bps: {t['slippage_bps']}\n"
                f"stop_loss_pct: {pf['stop_loss_pct']}\ntake_profit_pct: {pf['take_profit_pct']}\nmin_liquidity_sol: {pf['min_liquidity_sol']}\n"
                f"\n_Modifier:_ /config <cle> <valeur>"); return
        if len(c.args)==2:
            ok=self._set(c.args[0], c.args[1])
            await self._reply(u, f"\u2705 {c.args[0]}={c.args[1]}" if ok else f"\u274C Cle inconnue: {c.args[0]}")
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
        self.orch.set_paused(True); await self._reply(u, "\u23F8\uFE0F Pause")
    async def resume(self, u, c):
        if not self._auth(u): return
        self.orch.set_paused(False); await self._reply(u, "\u25B6\uFE0F Relance")