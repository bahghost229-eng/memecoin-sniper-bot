"""Agent 5 - Interface Telegram (commandes + notifications + boutons)."""
from telegram import (Update, BotCommand, ReplyKeyboardMarkup,
                      InlineKeyboardMarkup, InlineKeyboardButton)
from telegram.constants import ParseMode
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, filters, ContextTypes)
from utils import config_store
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
        [InlineKeyboardButton("\u2795 Wallet Pattern 1 (Direct)", callback_data="add_p1")],
        [InlineKeyboardButton("\u2795 Wallet Pattern 2 (Obfuscation)", callback_data="add_p2")],
        [InlineKeyboardButton("\u23F8\uFE0F Pause", callback_data="pause"),
         InlineKeyboardButton("\u25B6\uFE0F Resume", callback_data="resume")],
    ])

class TelegramInterface:
    def __init__(self, config, orchestrator):
        self.cfg=config; self.orch=orchestrator
        self.token=config["telegram"]["bot_token"]; self.chat_id=config["telegram"]["chat_id"]; self.app=None
        self._last_msg={}     # chat_id -> last bot message_id (pour suppression auto)
        self._pending={}      # chat_id -> "direct"|"obfuscation" (attente d'adresse)
    async def run(self):
        self.app=Application.builder().token(self.token).build()
        for cmd,fn in [("start",self.menu),("menu",self.menu),("status",self.status),("wallets",self.wallets),
                       ("add_wallet",self.add_wallet),("positions",self.positions),("config",self.config),
                       ("pause",self.pause),("resume",self.resume)]:
            self.app.add_handler(CommandHandler(cmd, fn))
        self.app.add_handler(CallbackQueryHandler(self.on_button))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
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
    async def _delete_last(self, chat_id):
        mid=self._last_msg.get(chat_id)
        if mid:
            try: await self.app.bot.delete_message(chat_id=chat_id, message_id=mid)
            except Exception: pass
            self._last_msg.pop(chat_id, None)
    async def _reply(self, u, text, kb=None, track=True):
        sent=await u.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        if track: self._last_msg[u.effective_chat.id]=sent.message_id
        return sent
    async def menu(self, u, c):
        if not self._auth(u): return
        await self._reply(u, "*\U0001F3AF Sniper Bot - Menu*\nUtilise les boutons ci-dessous :", inline_kb())
        await self._reply(u, "Clavier rapide active \U0001F447", REPLY_KB, track=False)
    async def on_button(self, u, c):
        if not self._auth(u): return
        q=u.callback_query; await q.answer()
        await self._delete_last(u.effective_chat.id)   # libere l'espace : supprime le precedent
        action=q.data
        if action=="add_p1":
            self._pending[u.effective_chat.id]="direct"
            await self._reply(u, "\u2795 *Pattern 1 (Direct)*\nEnvoie l'adresse du wallet financeur a suivre :"); return
        if action=="add_p2":
            self._pending[u.effective_chat.id]="obfuscation"
            await self._reply(u, "\u2795 *Pattern 2 (Obfuscation)*\nEnvoie l'adresse du wallet financeur a suivre :"); return
        fn={"status":self.status,"wallets":self.wallets,"positions":self.positions,
            "config":self.config,"pause":self.pause,"resume":self.resume}.get(action)
        if fn: await fn(u, c)
    async def on_text(self, u, c):
        if not self._auth(u): return
        chat=u.effective_chat.id
        pattern=self._pending.pop(chat, None)
        if not pattern: return   # texte hors contexte : ignore
        addr=(u.message.text or "").strip()
        if len(addr)<32 or len(addr)>44:
            await self._reply(u, "\u274C Adresse invalide. Reessaie via le bouton."); return
        self.orch.funding_monitor.add_funding_wallet(addr, pattern=pattern)
        label="Direct" if pattern=="direct" else "Obfuscation"
        await self._reply(u, "\u2705 Wallet ajoute (Pattern " + label + ") et souscrit en direct :\n`" + addr + "`")
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
        fmn=self.orch.funding_monitor
        lines=[]
        for w in fmn.funding_wallets:
            tag=fmn.wallet_patterns.get(w)
            suffix=" [P1]" if tag=="direct" else (" [P2]" if tag=="obfuscation" else "")
            lines.append("`" + w + "`" + suffix)
        await self._reply(u, "*Wallets surveilles:*\n"+("\n".join(lines) or "Aucun"))
    async def add_wallet(self, u, c):
        if not self._auth(u): return
        if not c.args: await self._reply(u, "Usage: /add_wallet <address>"); return
        self.orch.funding_monitor.add_funding_wallet(c.args[0])
        await self._reply(u, "\u2705 Ajoute: " + c.args[0])
    async def positions(self, u, c):
        if not self._auth(u): return
        ps=self.orch.portfolio.list_positions()
        if not ps: await self._reply(u, "Aucune position"); return
        await self._reply(u, "*Positions:*\n"+"\n".join(
            "`" + p["token"] + "` entree " + format(p["entry_price"],".10f") + " " + str(p["amount_sol"]) + " SOL" for p in ps))
    async def config(self, u, c):
        if not self._auth(u): return
        if not getattr(c,"args",None):
            t=self.cfg["trading"]; pf=self.cfg["portfolio"]
            await self._reply(u, "*Config*\nbuy_amount_sol: " + str(t["buy_amount_sol"]) + "\nslippage_bps: " + str(t["slippage_bps"])
                + "\nstop_loss_pct: " + str(pf["stop_loss_pct"]) + "\ntake_profit_pct: " + str(pf["take_profit_pct"])
                + "\nmin_liquidity_sol: " + str(pf["min_liquidity_sol"]) + "\n\n_Modifier:_ /config <cle> <valeur>"); return
        if len(c.args)==2:
            ok=self._set(c.args[0], c.args[1])
            await self._reply(u, ("\u2705 " + c.args[0] + "=" + c.args[1]) if ok else ("\u274C Cle inconnue: " + c.args[0]))
    def _set(self, key, val):
        m={"buy_amount_sol":("trading",float),"slippage_bps":("trading",int),"stop_loss_pct":("portfolio",float),
           "take_profit_pct":("portfolio",float),"min_liquidity_sol":("portfolio",float),
           "max_price_impact_pct":("portfolio",float)}
        if key not in m: return False
        sec,cast=m[key]; cv=cast(val); self.cfg[sec][key]=cv
        if sec=="trading":
            self.orch.token_sniper.buy_amount_sol=self.cfg["trading"]["buy_amount_sol"]
            self.orch.token_sniper.slippage_bps=self.cfg["trading"]["slippage_bps"]
        if sec=="portfolio":
            self.orch.portfolio.stop_loss=self.cfg["portfolio"]["stop_loss_pct"]
            self.orch.portfolio.take_profit=self.cfg["portfolio"]["take_profit_pct"]
            self.orch.portfolio.min_liquidity=self.cfg["portfolio"]["min_liquidity_sol"]
            self.orch.portfolio.max_impact=self.cfg["portfolio"].get("max_price_impact_pct", self.orch.portfolio.max_impact)
        try: config_store.save_override(sec, key, cv)   # persiste l'override (survit au redémarrage)
        except Exception as e: log.warning("config_persist_failed", extra={"error":str(e)})
        return True
    async def pause(self, u, c):
        if not self._auth(u): return
        self.orch.set_paused(True); await self._reply(u, "\u23F8\uFE0F Pause")
    async def resume(self, u, c):
        if not self._auth(u): return
        self.orch.set_paused(False); await self._reply(u, "\u25B6\uFE0F Relance")
