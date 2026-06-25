"""Chiffrement Fernet de la clé privée de trading."""
import os, getpass, sys, base64
import base58
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
SALT = b"memecoin_sniper_v1_salt"
def _key(passphrase, salt):
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=480000)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode()))
def encrypt_private_key(pk_b58, passphrase):
    return Fernet(_key(passphrase, SALT)).encrypt(pk_b58.encode()).decode()
def decrypt_private_key(token, passphrase):
    return base58.b58decode(Fernet(_key(passphrase, SALT)).decrypt(token.encode()).decode())
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
