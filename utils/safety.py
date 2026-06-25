"""Garde-fous anti-rug / honeypot partagés (sniper auto ET trading manuel)."""


async def check_token(helius, jupiter, mint, *, require_sellable=True,
                      block_freeze=True, block_mint=False):
    """Renvoie (ok: bool, reason: str|None). Refuse l'achat si dangereux."""
    auth = await helius.get_mint_authorities(mint)
    if auth:
        if block_freeze and auth.get("freeze_authority"):
            return False, "freeze authority active (gel possible / honeypot)"
        if block_mint and auth.get("mint_authority"):
            return False, "mint authority active (mint infini possible)"
    if require_sellable and not await jupiter.is_sellable(mint):
        return False, "aucune route de vente (honeypot probable)"
    return True, None
