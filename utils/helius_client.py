"""Client REST Helius : transactions, métadonnées, détection de patterns."""
import base64
import aiohttp
from utils import pumpfun
from utils.logger import get_logger
log = get_logger("helius_client")
LAMPORTS = 1_000_000_000

class HeliusClient:
    def __init__(self, cfg):
        self.api_key = cfg["api_key"]; self.rest_url = cfg["rest_url"]; self.rpc_url = cfg["rpc_url"]
        self._session = None
    async def _s(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        return self._session
    async def close(self):
        if self._session and not self._session.closed: await self._session.close()
    async def _rpc(self, method, params):
        s = await self._s()
        url = f"{self.rpc_url}/?api-key={self.api_key}"
        async with s.post(url, json={"jsonrpc":"2.0","id":1,"method":method,"params":params}) as r:
            r.raise_for_status(); return (await r.json()).get("result", {})
    async def get_transaction(self, sig):
        s = await self._s(); url = f"{self.rest_url}/transactions/?api-key={self.api_key}"
        try:
            async with s.post(url, json={"transactions":[sig]}) as r:
                r.raise_for_status(); data = await r.json(); return data[0] if data else None
        except Exception as e:
            log.warning("get_transaction_failed", extra={"sig":sig,"error":str(e)}); return None
    async def get_signatures(self, address, limit=25):
        res = await self._rpc("getSignaturesForAddress", [address, {"limit":limit}])
        return res if isinstance(res, list) else []
    async def is_fresh_wallet(self, address, exclude_sig=None):
        sigs = await self.get_signatures(address, limit=5)
        return len([s for s in sigs if s.get("signature")!=exclude_sig]) == 0
    @staticmethod
    def extract_native_transfers(tx): return tx.get("nativeTransfers", []) or []
    @staticmethod
    def extract_instructions(tx):
        ixs = list(tx.get("instructions", []) or [])
        for ix in tx.get("instructions", []) or []: ixs.extend(ix.get("innerInstructions", []) or [])
        return ixs
    @staticmethod
    def extract_mint_from_tx(tx):
        for tt in tx.get("tokenTransfers", []) or []:
            if tt.get("mint"): return tt["mint"]
        for acc in tx.get("accountData", []) or []:
            for ch in acc.get("tokenBalanceChanges", []) or []:
                if ch.get("mint"): return ch["mint"]
        return None
    async def get_account_info_b64(self, address):
        """Renvoie les octets bruts (base64 décodé) d'un compte, ou None."""
        res = await self._rpc("getAccountInfo", [address, {"encoding": "base64"}])
        val = (res or {}).get("value")
        if not val:
            return None
        data_field = val.get("data")
        if isinstance(data_field, list) and data_field:
            return base64.b64decode(data_field[0])
        return None
    async def get_pumpfun_liquidity_sol(self, mint):
        """Liquidité SOL réelle d'un token Pump.fun via la bonding curve on-chain.
        None si le compte n'existe pas ou si la bonding curve est terminée (migration AMM)."""
        try:
            data = await self.get_account_info_b64(pumpfun.bonding_curve_pda(mint))
            dec = pumpfun.decode_bonding_curve(data)
            if not dec or dec["complete"]:
                return None
            return pumpfun.bonding_curve_liquidity_sol(dec)
        except Exception as e:
            log.warning("pumpfun_liquidity_failed", extra={"mint": mint, "error": str(e)}); return None
    async def get_vault_sol_balance(self, vault):
        """Solde SOL/WSOL d'un vault de pool CONNU (Raydium), via getTokenAccountBalance."""
        try:
            res = await self._rpc("getTokenAccountBalance", [vault])
            amt = ((res or {}).get("value") or {}).get("amount")
            return int(amt) / LAMPORTS if amt is not None else None
        except Exception as e:
            log.warning("vault_balance_failed", extra={"vault": vault, "error": str(e)}); return None
    async def get_pool_liquidity_sol(self, mint, platform=None):
        """Liquidité SOL réelle. Pump.fun: réserves de la bonding curve (lecture directe).
        Raydium/Moonshot: nécessite la découverte du pool (vault WSOL) -> à compléter ;
        en attendant, PortfolioManager retombe sur le proxy price-impact."""
        if platform in ("pump.fun", None):
            liq = await self.get_pumpfun_liquidity_sol(mint)
            if liq is not None:
                return liq
        # TODO Raydium/Moonshot : résoudre le pool depuis le mint (index/API ou getProgramAccounts
        # avec memcmp), puis self.get_vault_sol_balance(quote_vault). Nécessite un test mainnet.
        return None
    async def get_mint_authorities(self, mint):
        """Renvoie {mint_authority, freeze_authority} via getAccountInfo jsonParsed, sinon None."""
        try:
            res = await self._rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}])
            info = ((((res or {}).get("value") or {}).get("data") or {}).get("parsed") or {}).get("info", {})
            return {"mint_authority": info.get("mintAuthority"), "freeze_authority": info.get("freezeAuthority")}
        except Exception as e:
            log.warning("mint_authorities_failed", extra={"mint": mint, "error": str(e)}); return None
    async def detect_bundling(self, mint, creation_sig):
        try:
            tx = await self.get_transaction(creation_sig)
            if not tx: return False
            slot = tx.get("slot"); sigs = await self.get_signatures(mint, limit=50)
            return len([s for s in sigs if s.get("slot")==slot]) >= 3
        except Exception: return False
    async def detect_lp_timing(self, creator, creation_sig):
        try:
            sigs = await self.get_signatures(creator, limit=10)
            return any("addLiquidity" in str(s.get("memo","")) for s in sigs)
        except Exception: return False
