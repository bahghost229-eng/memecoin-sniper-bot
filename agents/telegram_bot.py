"""Agent 5 - Interface Telegram : terminal de trading + sniper auto + notifications."""
from telegram import (Update, BotCommand, ReplyKeyboardMarkup,
                      InlineKeyboardMarkup, InlineKeyboardButton)
from telegram.constants import ParseMode
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, filters, ContextTypes)
from utils import config_store
from utils.logger import get_logger
log = get_logger("telegram")

BOT_COMMANDS = [
    BotCommand("menu", "Afficher le menu"),
    BotCommand("wallet", "Mon wallet (adresse + solde)"),
    BotCommand("buy", "Acheter un token (coller un mint)"),
    BotCommand("sell", "Vendre une position"),
    BotCommand("positions", "Positions ouvertes + PnL"),
    BotCommand("status", "Etat du bot"),
    BotCommand("wallets", "Funding wallets surveilles"),
    BotCommand("add_wallet", "Ajouter un wallet financeur"),
    BotCommand("config", "Voir/modifier la config"),
    BotCommand("pause", "Mettre en pause le sniper"),
    BotCommand("resume", "Reprendre le sniper"),
]

REPLY_KB = ReplyKeyboardMarkup(
    [["/wallet", "/positions"], ["/buy", "/sell"], ["/status", "/config"]],
    resize_keyboard=True)


def inline_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\U0001F4BC Wallet", callback_data="wallet"),
         InlineKeyboardButton("\U0001F4CA Positions", callback_data="positions")],
        [InlineKeyboardButton("\U0001F6D2 Acheter", callback_data="buy"),
         InlineKeyboardButton("\U0001F4C9 Vendre", callback_data="sell")],
        [InlineKeyboardButton("\U0001F4C8 Status", callback_data="status"),
         InlineKeyboardButton("⚙️ Config", callback_data="config")],
        [InlineKeyboardButton("➕ Funder Direct", callback_data="add_p1"),
         InlineKeyboardButton("➕ Funder Obf.", callback_data="add_p2")],
        [InlineKeyboardButton("⏸️ Pause", callback_data="pause"),
         InlineKeyboardButton("▶️ Resume", callback_data="resume")],
    ])


class TelegramInterface:
    def __init__(self, config, orchestrator):
        self.cfg=config; self.orch=orchestrator
        self.token=config["telegram"]["bot_token"]; self.chat_id=config["telegram"]["chat_id"]; self.app=None
        self._last_msg={}     # chat_id -> dernier message bot (suppression auto)
        self._pending={}      # chat_id -> "direct"|"obfuscation"|"buy_mint"|"buy_amount"
        self._ctx={}          # chat_id -> {"mint": ...} (contexte d'achat en cours)

    async def run(self):
        self.app=Application.builder().token(self.token).build()
        for cmd,fn in [("start",self.menu),("menu",self.menu),("status",self.status),("wallets",self.wallets),
                       ("add_wallet",self.add_wallet),("positions",self.positions),("config",self.config),
                       ("wallet",self.wallet),("buy",self.buy_start),("sell",self.sell_start),
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
        await self._reply(u, "*\U0001F3AF Sniper Bot - Menu*\nTerminal de trading + sniper auto :", inline_kb())
        await self._reply(u, "Clavier rapide \U0001F447", REPLY_KB, track=False)

    # -------- Routage des boutons --------
    async def on_button(self, u, c):
        if not self._auth(u): return
        q=u.callback_query; await q.answer()
        await self._delete_last(u.effective_chat.id)
        action=q.data
        if action=="add_p1": self._pending[u.effective_chat.id]="direct"; await self._reply(u, "➕ *Funder Direct*\nEnvoie l'adresse du wallet financeur :"); return
        if action=="add_p2": self._pending[u.effective_chat.id]="obfuscation"; await self._reply(u, "➕ *Funder Obfuscation*\nEnvoie l'adresse du wallet financeur :"); return
        if action=="buy": await self.buy_start(u, c); return
        if action=="sell": await self.sell_start(u, c); return
        if action.startswith("amt:"): await self._buy_amount_chosen(u, action.split(":",1)[1]); return
        if action.startswith("sellpct:"):
            _,mint,pct=action.split(":",2); await self._do_sell(u, mint, int(pct)); return
        if action.startswith("sell:"): await self._sell_pick_pct(u, action.split(":",1)[1]); return
        if action=="cancel": self._pending.pop(u.effective_chat.id,None); self._ctx.pop(u.effective_chat.id,None); await self._reply(u,"❌ Annulé."); return
        fn={"status":self.status,"wallets":self.wallets,"positions":self.positions,
            "config":self.config,"wallet":self.wallet,"pause":self.pause,"resume":self.resume}.get(action)
        if fn: await fn(u, c)

    async def on_text(self, u, c):
        if not self._auth(u): return
        chat=u.effective_chat.id
        mode=self._pending.pop(chat, None)
        if not mode: return
        txt=(u.message.text or "").strip()
        if mode in ("direct","obfuscation"):
            if len(txt)<32 or len(txt)>44: await self._reply(u, "❌ Adresse invalide."); return
            self.orch.funding_monitor.add_funding_wallet(txt, pattern=mode)
            label="Direct" if mode=="direct" else "Obfuscation"
            await self._reply(u, "✅ Funder ajoute (" + label + ") :\n`" + txt + "`"); return
        if mode=="buy_mint":
            if len(txt)<32 or len(txt)>44: await self._reply(u, "❌ Mint invalide. Recommence /buy."); return
            await self._show_token_info(u, txt); return
        if mode=="buy_amount":
            mint=(self._ctx.get(chat) or {}).get("mint")
            if not mint: await self._reply(u, "❌ Session expirée. Recommence /buy."); return
            try: amt=float(txt.replace(",","."))
            except ValueError: await self._reply(u, "❌ Montant invalide."); return
            await self._do_buy(u, mint, amt); return

    # -------- Wallet --------
    async def wallet(self, u, c):
        if not self._auth(u): return
        try:
            addr=self.orch.jupiter.wallet_pubkey()
        except Exception:
            await self._reply(u, "⚠️ Wallet non configuré (clé/passphrase manquante)."); return
        bal=await self.orch.jupiter.get_sol_balance()
        bal_str=f"{bal:.4f} SOL" if bal is not None else "n/d (dry-run ou RPC)"
        await self._reply(u, "*\U0001F4BC Wallet*\nAdresse :\n`"+addr+"`\nSolde : *"+bal_str+"*", inline_kb())

    # -------- Achat --------
    async def buy_start(self, u, c):
        if not self._auth(u): return
        self._pending[u.effective_chat.id]="buy_mint"
        await self._reply(u, "\U0001F6D2 *Acheter*\nColle l'adresse (mint) du token :")
    async def _show_token_info(self, u, mint):
        self._ctx[u.effective_chat.id]={"mint":mint}
        info=await self.orch.trader.token_info(mint)
        liq=info.get("liquidity_sol")
        liq_str=f"{liq:.2f} SOL" if liq is not None else "n/d"
        sell="✅" if info.get("sellable") else "❌"
        frz="⚠️ active" if info.get("freeze_authority") else "✅ aucune"
        presets=self.cfg["trading"].get("presets_sol",[0.1,0.5,1.0])
        rows=[[InlineKeyboardButton(f"{p} SOL", callback_data=f"amt:{p}") for p in presets],
              [InlineKeyboardButton("Montant libre", callback_data="amt:custom"),
               InlineKeyboardButton("Annuler", callback_data="cancel")]]
        await self._reply(u,
            "\U0001F6D2 *Token*\n`"+mint+"`\nLiquidité : "+liq_str+"\nVendable : "+sell+"\nFreeze authority : "+frz+
            "\n\nChoisis le montant :", InlineKeyboardMarkup(rows))
    async def _buy_amount_chosen(self, u, val):
        chat=u.effective_chat.id; mint=(self._ctx.get(chat) or {}).get("mint")
        if not mint: await self._reply(u, "❌ Session expirée. Recommence /buy."); return
        if val=="custom":
            self._pending[chat]="buy_amount"; await self._reply(u, "Envoie le montant en SOL (ex: 0.25) :"); return
        await self._do_buy(u, mint, float(val))
    async def _do_buy(self, u, mint, amount):
        await self._reply(u, f"⏳ Achat de *{amount} SOL* en cours...", track=False)
        res=await self.orch.trader.buy(mint, amount)
        self._ctx.pop(u.effective_chat.id, None)
        if res.get("blocked"): await self._reply(u, "\U0001F6E1️ Achat bloqué (sécurité) :\n"+str(res.get("error")), inline_kb()); return
        if not res.get("success"): await self._reply(u, "❌ Achat échoué : "+str(res.get("error")), inline_kb()); return
        await self._reply(u, "✅ *Acheté*\n`"+mint+"`\nMontant : "+str(amount)+" SOL\nTx : `"+str(res.get("tx_hash"))+"`", inline_kb())

    # -------- Vente --------
    async def sell_start(self, u, c):
        if not self._auth(u): return
        ps=self.orch.portfolio.list_positions()
        if not ps: await self._reply(u, "Aucune position à vendre.", inline_kb()); return
        rows=[[InlineKeyboardButton(p["token"][:10]+"…", callback_data="sell:"+p["token"])] for p in ps]
        await self._reply(u, "\U0001F4C9 *Vendre* — choisis une position :", InlineKeyboardMarkup(rows))
    async def _sell_pick_pct(self, u, mint):
        rows=[[InlineKeyboardButton("25%", callback_data=f"sellpct:{mint}:25"),
               InlineKeyboardButton("50%", callback_data=f"sellpct:{mint}:50"),
               InlineKeyboardButton("100%", callback_data=f"sellpct:{mint}:100")],
              [InlineKeyboardButton("Annuler", callback_data="cancel")]]
        await self._reply(u, "\U0001F4C9 Vendre `"+mint+"`\nQuelle part ?", InlineKeyboardMarkup(rows))
    async def _do_sell(self, u, mint, pct):
        await self._reply(u, f"⏳ Vente {pct}% en cours...", track=False)
        res=await self.orch.trader.sell(mint, pct)
        if not res.get("success"): await self._reply(u, "❌ Vente échouée : "+str(res.get("error")), inline_kb()); return
        await self._reply(u, "✅ *Vendu* "+str(pct)+"%\n`"+mint+"`\nTx : `"+str(res.get("tx_hash"))+"`", inline_kb())

    # -------- Status / infos --------
    async def status(self, u, c):
        if not self._auth(u): return
        etat = "⏸️ PAUSE" if self.orch.is_paused() else "▶️ ACTIF"
        mode = "\U0001F9EA DRY-RUN" if self.cfg["general"]["dry_run"] else "\U0001F534 LIVE"
        nw = len(self.orch.funding_monitor.funding_wallets)
        nt = len(self.orch.pattern_detector.tracked)
        npos = len(self.orch.portfolio.list_positions())
        msg = ("*Sniper Bot*\nEtat: "+etat+"\nMode: "+mode+"\nFunders: "+str(nw)
               +"\nTraces: "+str(nt)+"\nPositions: "+str(npos))
        await self._reply(u, msg)
    async def wallets(self, u, c):
        if not self._auth(u): return
        fmn=self.orch.funding_monitor; lines=[]
        for w in fmn.funding_wallets:
            tag=fmn.wallet_patterns.get(w)
            suffix=" [P1]" if tag=="direct" else (" [P2]" if tag=="obfuscation" else "")
            lines.append("`"+w+"`"+suffix)
        await self._reply(u, "*Funders surveillés:*\n"+("\n".join(lines) or "Aucun"))
    async def add_wallet(self, u, c):
        if not self._auth(u): return
        if not c.args: await self._reply(u, "Usage: /add_wallet <address>"); return
        self.orch.funding_monitor.add_funding_wallet(c.args[0])
        await self._reply(u, "✅ Ajouté: "+c.args[0])
    async def positions(self, u, c):
        if not self._auth(u): return
        ps=self.orch.portfolio.list_positions()
        if not ps: await self._reply(u, "Aucune position", inline_kb()); return
        lines=[]
        for p in ps:
            cost=p.get("amount_sol",0) or 0
            val=await self.orch.jupiter.get_position_value(p["token"], p.get("amount_tokens",0))
            if val:
                pnl=((val["value_sol"]-cost)/cost*100) if cost else 0
                lines.append("`"+p["token"][:12]+"…` "+f"{cost} SOL → {val['value_sol']:.4f} SOL ({pnl:+.1f}%)")
            else:
                lines.append("`"+p["token"][:12]+"…` "+str(cost)+" SOL (valeur n/d)")
        await self._reply(u, "*Positions:*\n"+"\n".join(lines), inline_kb())
    async def config(self, u, c):
        if not self._auth(u): return
        if not getattr(c,"args",None):
            t=self.cfg["trading"]; pf=self.cfg["portfolio"]
            await self._reply(u, "*Config*\nbuy_amount_sol: "+str(t["buy_amount_sol"])+"\nslippage_bps: "+str(t["slippage_bps"])
                + "\nstop_loss_pct: "+str(pf["stop_loss_pct"])+"\ntake_profit_pct: "+str(pf["take_profit_pct"])
                + "\nmin_liquidity_sol: "+str(pf["min_liquidity_sol"])+"\n\n_Modifier:_ /config <cle> <valeur>"); return
        if len(c.args)==2:
            ok=self._set(c.args[0], c.args[1])
            await self._reply(u, ("✅ "+c.args[0]+"="+c.args[1]) if ok else ("❌ Cle inconnue: "+c.args[0]))
    def _set(self, key, val):
        m={"buy_amount_sol":("trading",float),"slippage_bps":("trading",int),"stop_loss_pct":("portfolio",float),
           "take_profit_pct":("portfolio",float),"min_liquidity_sol":("portfolio",float),
           "max_price_impact_pct":("portfolio",float)}
        if key not in m: return False
        sec,cast=m[key]; cv=cast(val); self.cfg[sec][key]=cv
        if sec=="trading":
            self.orch.token_sniper.buy_amount_sol=self.cfg["trading"]["buy_amount_sol"]
            self.orch.token_sniper.slippage_bps=self.cfg["trading"]["slippage_bps"]
            self.orch.trader.slippage=self.cfg["trading"]["slippage_bps"]
        if sec=="portfolio":
            self.orch.portfolio.stop_loss=self.cfg["portfolio"]["stop_loss_pct"]
            self.orch.portfolio.take_profit=self.cfg["portfolio"]["take_profit_pct"]
            self.orch.portfolio.min_liquidity=self.cfg["portfolio"]["min_liquidity_sol"]
            self.orch.portfolio.max_impact=self.cfg["portfolio"].get("max_price_impact_pct", self.orch.portfolio.max_impact)
        try: config_store.save_override(sec, key, cv)
        except Exception as e: log.warning("config_persist_failed", extra={"error":str(e)})
        return True
    async def pause(self, u, c):
        if not self._auth(u): return
        self.orch.set_paused(True); await self._reply(u, "⏸️ Pause")
    async def resume(self, u, c):
        if not self._auth(u): return
        self.orch.set_paused(False); await self._reply(u, "▶️ Relance")
