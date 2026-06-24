"""Client Jupiter Aggregator : quotes + swaps signés et envoyés on-chain."""
import os, base64
import aiohttp
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solders.commitment_config import CommitmentLevel
from solders.rpc.requests import SendVersionedTransaction
from solders.rpc.config import RpcSendTransactionConfig
from utils.crypto import decrypt_private_key
from utils.logger import get_logger
log = get_logger("jupiter_client")
LAMPORTS = 1_000_000_000

class JupiterClient:
    def __init__(self, jcfg, tcfg, wcfg, dry_run=True):
        self.quote_url=jcfg["quote_url"]; self.swap_url=jcfg["swap_url"]; self.sol_mint=jcfg["sol_mint"]
        self.priority_fee=tcfg.get("priority_fee_lamports",0); self.dry_run=dry_run
        self._session=None; self._kp=None; self._wcfg=wcfg
        self._rpc=os.environ.get("HELIUS_RPC_URL","https://api.mainnet-beta.solana.com")
    def _keypair(self):
        if self._kp is None:
            raw = decrypt_private_key(self._wcfg["encrypted_private_key"], os.environ["SNIPER_KEY_PASSPHRASE"])
            self._kp = Keypair.from_bytes(raw)
        return self._kp
    async def _s(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        return self._session
    async def close(self):
        if self._session and not self._session.closed: await self._session.close()
    async def get_quote(self, inp, out, amount, slippage_bps):
        s = await self._s()
        params={"inputMint":inp,"outputMint":out,"amount":str(amount),"slippageBps":str(slippage_bps)}
        async with s.get(self.quote_url, params=params) as r:
            if r.status!=200: log.warning("quote_failed", extra={"status":r.status}); return None
            return await r.json()
    async def get_price(self, mint):
        q = await self.get_quote(mint, self.sol_mint, 1_000_000, 500)
        if not q or "outAmount" not in q: return None
        return int(q["outAmount"]) / LAMPORTS
    async def _swap(self, quote):
        if self.dry_run:
            log.info("dry_run_swap", extra={"out":quote.get("outAmount")})
            return {"success":True,"tx_hash":"DRYRUN_"+quote.get("outAmount","0"),"out_amount":int(quote.get("outAmount",0))}
        s = await self._s(); kp = self._keypair()
        body={"quoteResponse":quote,"userPublicKey":str(kp.pubkey()),"wrapAndUnwrapSol":True,
              "prioritizationFeeLamports":self.priority_fee or "auto","dynamicComputeUnitLimit":True}
        async with s.post(self.swap_url, json=body) as r:
            r.raise_for_status(); sd = await r.json()
        unsigned = VersionedTransaction.from_bytes(base64.b64decode(sd["swapTransaction"]))
        signed = VersionedTransaction(unsigned.message, [kp])
        req = SendVersionedTransaction(signed, RpcSendTransactionConfig(preflight_commitment=CommitmentLevel.Confirmed))
        async with s.post(self._rpc, data=req.to_json(), headers={"Content-Type":"application/json"}) as r:
            res = await r.json()
        h = res.get("result")
        return {"success":bool(h),"tx_hash":h,"out_amount":int(quote["outAmount"])} if h else {"success":False,"error":str(res.get("error"))}
    async def buy(self, output_mint, amount_sol, slippage_bps):
        amount=int(amount_sol*LAMPORTS); q=await self.get_quote(self.sol_mint, output_mint, amount, slippage_bps)
        if not q: return {"success":False,"error":"no_quote"}
        out=int(q["outAmount"]); price=amount/out if out else 0
        res=await self._swap(q)
        if res["success"]: res["price"]=price; res["out_amount"]=out
        return res
    async def sell(self, input_mint, amount_tokens, slippage_bps):
        q=await self.get_quote(input_mint, self.sol_mint, amount_tokens, slippage_bps)
        if not q: return {"success":False,"error":"no_quote"}
        return await self._swap(q)
