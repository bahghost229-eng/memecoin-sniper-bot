"""Détecteurs de patterns - fonctions pures testables sur données type-Helius."""
from typing import Optional

LAMPORTS_PER_SOL = 1_000_000_000

PUMP_FUN = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
RAYDIUM = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
MOONSHOT = "MoonCVVNZFSYkqNXP6bxHLPL6QQJiMagDL3qcqUQTrG"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
CREATION_PROGRAMS = {PUMP_FUN, RAYDIUM, MOONSHOT}

PLATFORM = {PUMP_FUN: "pump.fun", RAYDIUM: "raydium", MOONSHOT: "moonshot"}


def native_transfers(tx: dict) -> list:
    return tx.get("nativeTransfers", []) or []


def instructions(tx: dict) -> list:
    out = list(tx.get("instructions", []) or [])
    for ix in tx.get("instructions", []) or []:
        out.extend(ix.get("innerInstructions", []) or [])
    return out


def is_fresh_wallet(signatures: list, exclude_sig: Optional[str] = None) -> bool:
    """Fresh = aucune signature antérieure (hors la tx de financement)."""
    relevant = [s for s in signatures if s.get("signature") != exclude_sig]
    return len(relevant) == 0


def detect_funding(tx: dict, funding_wallets: set, min_sol: float, max_sol: float):
    """Retourne (dst, amount_sol, sig) si un financeur envoie min..max SOL, sinon None."""
    for tr in native_transfers(tx):
        src, dst = tr["fromUserAccount"], tr["toUserAccount"]
        amount = tr["amount"] / LAMPORTS_PER_SOL
        if src in funding_wallets and min_sol <= amount <= max_sol:
            return {"wallet": dst, "amount_sol": round(amount, 4), "source": src,
                    "signature": tx.get("signature")}
    return None


def detect_token_creation(tx: dict):
    """Détecte création via Pump.fun/Raydium/Moonshot ou initializeMint SPL."""
    for ix in instructions(tx):
        prog = ix.get("programId", "")
        if prog in CREATION_PROGRAMS:
            mint = _mint_from_tx(tx)
            return {"mint": mint, "program": prog, "platform": PLATFORM.get(prog, "unknown")}
        if prog == TOKEN_PROGRAM and ix.get("parsed", {}).get("type") == "initializeMint":
            return {"mint": ix["parsed"]["info"].get("mint"), "program": prog, "platform": "spl"}
    return None


def _mint_from_tx(tx: dict):
    for tt in tx.get("tokenTransfers", []) or []:
        if tt.get("mint"):
            return tt["mint"]
    return None


def detect_bundling(creation_slot: int, related_sigs: list, threshold: int = 3) -> bool:
    """Bundling = >= threshold transactions (achats) dans le même slot que la création."""
    same_slot = [s for s in related_sigs if s.get("slot") == creation_slot]
    return len(same_slot) >= threshold


def detect_lp_timing(creator_sigs: list, max_gap_sec: int = 30) -> bool:
    """LP timing = addLiquidity puis création de token rapprochés (<= max_gap_sec)."""
    lp = [s for s in creator_sigs if s.get("action") == "addLiquidity"]
    create = [s for s in creator_sigs if s.get("action") == "createToken"]
    for l in lp:
        for c in create:
            if 0 <= c["ts"] - l["ts"] <= max_gap_sec:
                return True
    return False


def detect_vanity(wallet: str, prefixes: list) -> Optional[str]:
    low = wallet.lower()
    for p in prefixes:
        if low.startswith(p.lower()):
            return p
    return None
