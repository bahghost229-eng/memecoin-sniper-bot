# 🎯 Solana Memecoin Sniping Bot

Bot multi-agents de sniping de memecoins Solana. Détecte les wallets financeurs,
trace les patterns de lancement (direct / brouillage / bundling / LP timing / vanity),
et snipe automatiquement les nouveaux tokens via Jupiter.

> ⚠️ **Avertissement** : trading à très haut risque (rug-pulls fréquents). Code fourni
> à but éducatif. Teste en `dry_run` avant tout. Vérifie la conformité réglementaire.

## Architecture (5 agents)

| Agent | Rôle |
|---|---|
| FundingWalletMonitor | WebSocket : détecte financement 1.5–5 SOL vers fresh wallets |
| PatternDetector | direct, brouillage (graphe de convergence 10 min), bundling, LP timing, vanity |
| TokenSniper | achat immédiat via Jupiter (slippage configurable) |
| PortfolioManager | stop-loss / take-profit / liquidité auto-sell |
| TelegramInterface | /status /wallets /add_wallet /positions /config /pause /resume |

## Statut des tests

- ✅ Tests logiques (simulation) : **25/25**
- ✅ End-to-end devnet réel (tokens SPL) : **5/5**
- ✅ Replay mainnet réel (créations Pump.fun) : **4/4**

## Structure

```
memecoin_sniper/
├── main.py
├── orchestrator.py
├── agents/{funding_monitor,pattern_detector,token_sniper,portfolio_manager,telegram_bot}.py
├── utils/{helius_client,jupiter_client,wallet_graph,detectors,crypto,logger}.py
├── config.example.yaml
├── requirements.txt
└── systemd/sniper.service
```

## Installation locale

```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp config.example.yaml config.yaml                 # puis remplir les clés
python -m utils.crypto encrypt                     # chiffrer la clé privée -> config.yaml
export SNIPER_KEY_PASSPHRASE="ta_passphrase"
python main.py
```

## Déploiement Kamatera (Ubuntu 22.04, 24/7)

```bash
# 1. Utilisateur + dossier
sudo adduser --system --group sniper
sudo mkdir -p /opt/memecoin_sniper && sudo chown sniper:sniper /opt/memecoin_sniper

# 2. Cloner + venv
cd /opt/memecoin_sniper
sudo -u sniper git clone https://github.com/bahghost229-eng/solana-memecoin-sniping-bot .
sudo -u sniper python3 -m venv venv
sudo -u sniper ./venv/bin/pip install -r requirements.txt

# 3. Config + secrets
sudo -u sniper cp config.example.yaml config.yaml
sudo -u sniper nano config.yaml                    # clés Helius, Telegram, wallets
sudo -u sniper ./venv/bin/python -m utils.crypto encrypt   # -> encrypted_private_key
sudo chmod 600 config.yaml

# 4. systemd (passphrase + clé RPC dans le service)
sudo cp systemd/sniper.service /etc/systemd/system/
sudo nano /etc/systemd/system/sniper.service       # SNIPER_KEY_PASSPHRASE, HELIUS_RPC_URL
sudo systemctl daemon-reload
sudo systemctl enable --now sniper

# 5. Logs JSON
journalctl -u sniper -f -o cat
```

## Sécurité

- `config.yaml`, `helius_key.txt`, `*.json` de wallets sont **gitignore**.
- Clé privée chiffrée (Fernet) ; passphrase via variable d'env uniquement.
- `chmod 600 config.yaml` sur le serveur.
