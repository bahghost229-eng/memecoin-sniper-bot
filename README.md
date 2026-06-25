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
| TelegramInterface | Terminal manuel (/wallet /buy /sell /positions) + sniper (/status /wallets /add_wallet /config /pause /resume) |

## Terminal de trading (Telegram)

En plus du sniper automatique, le bot expose un **terminal manuel** style Trojan/Tradewiz :

- `/wallet` — adresse du wallet de trading + solde SOL.
- `/buy` — colle un **mint** → infos (liquidité, vendable, freeze authority) → boutons montant
  (presets `trading.presets_sol` ou montant libre). Achat passé par le **garde-fou anti-honeypot**.
- `/sell` — liste les positions → vente **25 / 50 / 100 %** sur le **solde réel on-chain**.
- `/positions` — positions ouvertes avec **PnL en direct** (valeur réalisable vs coût).

Le sniper automatique (funding wallets → snipe) reste actif en parallèle. Boutons inline via `/menu`.

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

# 4. Secrets hors de l'unité systemd (credentials + EnvironmentFile 600)
sudo install -d -m 700 /etc/sniper
printf '%s' 'TA_PASSPHRASE' | sudo tee /etc/sniper/passphrase >/dev/null
echo 'HELIUS_RPC_URL=https://mainnet.helius-rpc.com/?api-key=VOTRE_CLE' | sudo tee /etc/sniper/env >/dev/null
sudo chmod 600 /etc/sniper/passphrase /etc/sniper/env

# 5. systemd (l'unité ne contient AUCUN secret)
sudo cp systemd/sniper.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sniper

# 6. Logs JSON
journalctl -u sniper -f -o cat
```

## Sécurité

- `config.yaml`, `helius_key.txt`, `*.json` de wallets sont **gitignore**.
- Clé privée chiffrée (Fernet, PBKDF2 480k itérations).
- **Passphrase hors de l'unité systemd** : via `SNIPER_KEY_PASSPHRASE` (env) ou
  `SNIPER_KEY_PASSPHRASE_FILE` (credential systemd sur tmpfs). Ne jamais l'écrire dans `.service`.
- `chmod 600` sur `/etc/sniper/passphrase`, `/etc/sniper/env` et `config.yaml`.

## Garde-fous trading (anti-rug / honeypot)

- **Avant chaque achat** : refus si la *freeze authority* du mint est active, et refus si le token
  n'a **aucune route de vente** (honeypot probable). Cf. section `safety` de la config.
- **Swaps confirmés on-chain** (`getSignatureStatuses`) avant d'ouvrir une position.
- **Liquidité réelle (Pump.fun)** : lecture des réserves SOL de la bonding curve on-chain ; revente
  si elles passent sous `min_liquidity_sol`. Repli **impact de prix** (`max_price_impact_pct`) pour
  les plateformes dont le pool n'est pas encore lu (Raydium : découverte de pool à compléter).
- **Vente sur solde réel** : la quantité vendue vient du solde on-chain (`getTokenAccountsByOwner`),
  pas du montant estimé à l'achat ; position clôturée si le solde tombe à zéro.
- PnL calculé sur la **valeur réalisable** (revente simulée → SOL) vs le coût, pas sur des prix d'unités hétérogènes.

## Tests

```bash
python -m unittest discover -s tests -v
```
