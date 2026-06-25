"""Client Jupiter Aggregator : quotes + swaps signés, envoyés ET confirmés on-chain."""
import os, base64, asyncio
import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solders.commitment_config import CommitmentLevel
from solders.rpc.requests import SendVersionedTransaction
from solders.rpc.config import RpcSendTransactionConfig
from utils.crypto import decrypt_private_key, get_passphrase
from utils.logger import get_logger
log = get_logger("jupiter_client")
LAMPORTS = 1_000_000_000

class JupiterClient:
    def __init__(self, jcfg, tcfg, wcfg, dry_run=True):
        self.quote_url=jcfg["quote_url"]; self.swap_url=jcfg["swap_url"]; self.sol_mint=jcfg["sol_mint"]
        self.priority_fee=tcfg.get("priority_fee_lamports",0); self.dry_run=dry_run
        self._session=None; self._kp=None; self._wcfg=wcfg
        self._rpc=os.environ.get("HELIUS_RPC_URL","https://api.mainnet-beta.solana.com")
        self.confirm_timeout=tcfg.get("confirm_timeout_sec",45)
        self.confirm_poll=tcfg.get("confirm_poll_sec",2)
    def _keypair(self):
        if self._kp is None:
            raw = decrypt_private_key(self._wcfg["encrypted_private_key"], get_passphrase())
            self._kp = Keypair.from_bytes(raw)
        return self._kp
    async def _s(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        return self._session
    async def close(self):
        if self._session and not self._session.closed: await self._session.close()
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.3, max=2),
           retry=retry_if_exception_type(aiohttp.ClientError))
    async def _quote_once(self, params):
        s = await self._s()
        async with s.get(self.quote_url, params=params) as r:
            if r.status >= 500:               # transitoire -> retry
                raise aiohttp.ClientError(f"quote {r.status}")
            if r.status != 200:               # 4xx (ex: no route) -> pas de quote
                log.warning("quote_failed", extra={"status": r.status}); return None
            return await r.json()
    async def get_quote(self, inp, out, amount, slippage_bps):
        params={"inputMint":inp,"outputMint":out,"amount":str(amount),"slippageBps":str(slippage_bps)}
        try:
            return await self._quote_once(params)
        except Exception as e:
            log.warning("quote_error", extra={"error":str(e)}); return None
    async def get_price(self, mint, probe_units=1_000_000):
        """Prix réalisable en lamports de SOL PAR UNITÉ DE BASE du token.
        Même unité que entry_price (= amount_lamports / out_units au buy)."""
        q = await self.get_quote(mint, self.sol_mint, probe_units, 500)
        if not q or not q.get("outAmount"): return None
        out = int(q["outAmount"])
        if out <= 0: return None
        return out / probe_units
    async def get_position_value(self, mint, amount_tokens):
        """Valeur réalisable d'une position : simule la revente de amount_tokens -> SOL.
        Renvoie {value_sol, price_impact_pct} ou None. C'est la base d'un PnL correct."""
        if not amount_tokens or amount_tokens <= 0: return None
        q = await self.get_quote(mint, self.sol_mint, int(amount_tokens), 500)
        if not q or not q.get("outAmount"): return None
        value_sol = int(q["outAmount"]) / LAMPORTS
        impact = abs(float(q.get("priceImpactPct", 0) or 0)) * 100.0
        return {"value_sol": value_sol, "price_impact_pct": impact}
    async def is_sellable(self, mint, probe_units=1_000_000):
        """Anti-honeypot : existe-t-il une route de vente token -> SOL ?"""
        q = await self.get_quote(mint, self.sol_mint, probe_units, 1500)
        return bool(q and q.get("outAmount") and int(q["outAmount"]) > 0)
    async def _send_raw(self, signed):
        s = await self._s()
        req = SendVersionedTransaction(signed, RpcSendTransactionConfig(preflight_commitment=CommitmentLevel.Confirmed))
        async with s.post(self._rpc, data=req.to_json(), headers={"Content-Type":"application/json"}) as r:
            return await r.json()
    async def _confirm(self, sig):
        """Poll getSignatureStatuses jusqu'à confirmed/finalized, échec on-chain, ou timeout."""
        s = await self._s(); waited = 0.0
        while waited < self.confirm_timeout:
            body = {"jsonrpc":"2.0","id":1,"method":"getSignatureStatuses",
                    "params":[[sig], {"searchTransactionHistory": True}]}
            try:
                async with s.post(self._rpc, json=body) as r:
                    res = (await r.json()).get("result", {})
                val = (res.get("value") or [None])[0]
                if val:
                    if val.get("err") is not None:
                        return {"confirmed": False, "err": val["err"]}
                    if val.get("confirmationStatus") in ("confirmed", "finalized"):
                        return {"confirmed": True, "err": None}
            except Exception as e:
                log.warning("confirm_poll_error", extra={"error": str(e)})
            await asyncio.sleep(self.confirm_poll); waited += self.confirm_poll
        return {"confirmed": False, "err": "confirmation_timeout"}
    async def _swap(self, quote):
        if self.dry_run:
            log.info("dry_run_swap", extra={"out":quote.get("outAmount")})
            return {"success":True,"tx_hash":"DRYRUN_"+str(quote.get("outAmount","0")),"out_amount":int(quote.get("outAmount",0))}
        s = await self._s(); kp = self._keypair()
        body={"quoteResponse":quote,"userPublicKey":str(kp.pubkey()),"wrapAndUnwrapSol":True,
              "prioritizationFeeLamports":self.priority_fee or "auto","dynamicComputeUnitLimit":True}
        async with s.post(self.swap_url, json=body) as r:
            r.raise_for_status(); sd = await r.json()
        unsigned = VersionedTransaction.from_bytes(base64.b64decode(sd["swapTransaction"]))
        signed = VersionedTransaction(unsigned.message, [kp])
        res = await self._send_raw(signed)
        h = res.get("result")
        if not h:
            return {"success":False,"error":str(res.get("error"))}
        conf = await self._confirm(h)           # ne valide la position qu'une fois la tx réellement landée
        if not conf["confirmed"]:
            return {"success":False,"tx_hash":h,"error":f"unconfirmed: {conf['err']}"}
        return {"success":True,"tx_hash":h,"out_amount":int(quote["outAmount"])}
    async def buy(self, output_mint, amount_sol, slippage_bps):
        amount=int(amount_sol*LAMPORTS); q=await self.get_quote(self.sol_mint, output_mint, amount, slippage_bps)
        if not q: return {"success":False,"error":"no_quote"}
        out=int(q["outAmount"]); price=amount/out if out else 0   # lamports SOL / unité de token
        res=await self._swap(q)
        if res["success"]: res["price"]=price; res["out_amount"]=out
        return res
    async def sell(self, input_mint, amount_tokens, slippage_bps):
        q=await self.get_quote(input_mint, self.sol_mint, int(amount_tokens), slippage_bps)
        if not q: return {"success":False,"error":"no_quote"}
        return await self._swap(q)
