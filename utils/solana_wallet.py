"""Génération / import de wallet Solana (base58 secret façon Phantom)."""
from __future__ import annotations
import base58
from solders.keypair import Keypair


def generate_keypair():
    """Crée un nouveau wallet. Renvoie (pubkey_str, secret_base58)."""
    kp = Keypair()
    return str(kp.pubkey()), base58.b58encode(bytes(kp)).decode()


def pubkey_from_secret(secret_b58: str) -> str:
    """Adresse publique à partir d'une clé secrète base58 (64 octets)."""
    return str(Keypair.from_bytes(base58.b58decode(secret_b58)).pubkey())
