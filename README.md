# Solana Sniper Scanner - Humanized Edition

Scanner pentru memecoins Solana cu:
- discovery din DexScreener
- scoring dinamic
- wallet tracker mai robust
- whale tracking bazat pe fluxuri reale din tranzactii
- anti-rug heuristics pentru dump de dev / early wallets
- semnale PREPUMP / WATCH / HOT / EXIT
- mesaj Telegram mai "uman"

## Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run.py
```

## Ce face in plus

- marcheaza primele wallet-uri relevante ca early buyers cand timestamp-ul pair-ului e nesigur
- incearca sa detecteze whale accumulation vs whale dumping
- construieste un verdict uman: `relativ ok`, `riscant`, `posibil rug`, `ia profit`, etc.
- trimite si semnale de EXIT, nu doar de entry

## Limitari importante

- `anti-rug` inseamna euristici, nu garantie
- fara integrare Pump.fun dedicata, `dev wallet` este aproximat din primele wallet-uri dominante si din comportamentul early holders
- whale tracking foloseste fluxurile observate pe mint + scoring istoric local
