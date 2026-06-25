"""Lecture on-chain de la bonding curve Pump.fun (réserves réelles de SOL).

La bonding curve est un compte PDA dérivé de [b"bonding-curve", mint]. Son data (Anchor)
contient, après le discriminator de 8 octets, 5 u64 little-endian puis un booléen :

    virtual_token_reserves : u64
    virtual_sol_reserves   : u64
    real_token_reserves    : u64
    real_sol_reserves      : u64   <- liquidité SOL réelle (lamports)
    token_total_supply     : u64
    complete               : bool  (1 = bonding curve terminée -> migration AMM)

Les versions récentes ajoutent un champ `creator` à la fin : sans impact, on ne lit
que les 49 premiers octets.
"""
from __future__ import annotations
import struct
from solders.pubkey import Pubkey

PUMP_FUN_PROGRAM = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
BONDING_SEED = b"bonding-curve"
LAMPORTS = 1_000_000_000
_LAYOUT = struct.Struct("<QQQQQ")   # 5 u64 après le discriminator


def bonding_curve_pda(mint: str) -> str:
    """PDA de la bonding curve pour un mint donné (déterministe)."""
    mint_pk = Pubkey.from_string(mint)
    pda, _bump = Pubkey.find_program_address([BONDING_SEED, bytes(mint_pk)], PUMP_FUN_PROGRAM)
    return str(pda)


def decode_bonding_curve(data: bytes | None) -> dict | None:
    """Décode le compte bonding curve. Renvoie None si data absente/trop courte."""
    if not data or len(data) < 49:
        return None
    vtok, vsol, rtok, rsol, supply = _LAYOUT.unpack_from(data, 8)
    return {
        "virtual_token_reserves": vtok,
        "virtual_sol_reserves": vsol,
        "real_token_reserves": rtok,
        "real_sol_reserves": rsol,
        "token_total_supply": supply,
        "complete": bool(data[48]),
    }


def bonding_curve_liquidity_sol(decoded: dict) -> float:
    """Liquidité SOL réelle (conservatrice) = real_sol_reserves en SOL."""
    return decoded["real_sol_reserves"] / LAMPORTS
