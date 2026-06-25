"""Chiffrement Fernet de la clé privée de trading.

Format v2 : salt aléatoire PAR INSTALLATION, préfixé au token -> "v2:<b64(salt)>:<token>".
Rétro-compatible : un token sans préfixe est déchiffré avec l'ancien salt statique.
"""
import os, getpass, sys, base64, secrets
import base58
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

SALT_LEGACY = b"memecoin_sniper_v1_salt"   # clés chiffrées avant v2 (ne pas supprimer)
SALT_LEN = 16
PREFIX = "v2:"
ITERATIONS = 480000

def _key(passphrase, salt):
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))

def encrypt_private_key(pk_b58, passphrase):
    salt = secrets.token_bytes(SALT_LEN)
    token = Fernet(_key(passphrase, salt)).encrypt(pk_b58.encode()).decode()
    return PREFIX + base64.urlsafe_b64encode(salt).decode() + ":" + token

def decrypt_private_key(stored, passphrase):
    if stored.startswith(PREFIX):
        _, salt_b64, token = stored.split(":", 2)
        salt = base64.urlsafe_b64decode(salt_b64)
    else:
        salt, token = SALT_LEGACY, stored          # rétro-compat : clés à salt statique
    return base58.b58decode(Fernet(_key(passphrase, salt)).decrypt(token.encode()).decode())

def get_passphrase():
    """Passphrase via SNIPER_KEY_PASSPHRASE, ou fichier pointé par SNIPER_KEY_PASSPHRASE_FILE
    (ex: credential systemd dans $CREDENTIALS_DIRECTORY). Jamais en clair dans l'unité systemd."""
    p = os.environ.get("SNIPER_KEY_PASSPHRASE")
    if p:
        return p
    fp = os.environ.get("SNIPER_KEY_PASSPHRASE_FILE")
    if fp and os.path.exists(fp):
        with open(fp, "r", encoding="utf-8") as f:
            return f.read().strip()
    raise RuntimeError("Passphrase absente : définir SNIPER_KEY_PASSPHRASE ou SNIPER_KEY_PASSPHRASE_FILE")

if __name__ == "__main__":
    if len(sys.argv)>1 and sys.argv[1]=="encrypt":
        pk = getpass.getpass("Cle privee (base58): "); pw = getpass.getpass("Passphrase: ")
        print("\nencrypted_private_key:\n"+encrypt_private_key(pk, pw))
    else: print("Usage: python -m utils.crypto encrypt")
