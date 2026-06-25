import json
import os
import time
from datetime import datetime
from pathlib import Path

import requests
from google import genai

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHANNEL = os.environ.get("TELEGRAM_CHANNEL", "@btc_signal_confirme_2026")
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
ANALYSIS_MODEL = os.environ.get("ANALYSIS_MODEL", "gemini-2.5-flash-lite")

POLL_SECONDS = 60
ANALYSIS_INTERVAL_SECONDS = 3600
DAILY_LEVELS_INTERVAL_SECONDS = 86400
STATE_FILE = Path(__file__).parent / "state.json"

# Niveaux de confirmation Or/XAUUSD — structure SMC juin 2026
LEVELS = [
    {
        "price": 3959,
        "direction": "below",
        "message": (
            "🟠 Or/XAUUSD a clôturé sous 3 959 $ (PDL) — Confirmation SHORT\n"
            "SL : 4 015 $ | Objectif 1 : 3 900 $ | Objectif 2 : 3 847 $"
        ),
    },
    {
        "price": 4115,
        "direction": "above",
        "message": (
            "🔵 Or/XAUUSD a clôturé au-dessus de 4 115 $ (PDH) — Confirmation LONG\n"
            "SL : 3 970 $ | Objectif 1 : 4 250 $ | Objectif 2 : 4 405 $"
        ),
    },
    {"price": 4250, "direction": "above", "message": "🎯 Objectif LONG 1 atteint : 4 250 $"},
    {"price": 4405, "direction": "above", "message": "🎯 Objectif LONG 2 atteint : 4 405 $"},
    {"price": 3900, "direction": "below", "message": "🎯 Objectif SHORT 1 atteint : 3 900 $"},
    {"price": 3847, "direction": "below", "message": "🎯 Objectif SHORT 2 atteint : 3 847 $"},
]

STRATEGY_CONTEXT = """
Contexte stratégie Or/XAUUSD (SMC + intermarché) :
- Structure : BOS baissier depuis le sommet 4 428, cassures successives vers le bas.
- PDH (hier haut) = 4 115 | PDL (hier bas) = 3 959. Zone neutre actuelle ~3 988.
- Scénario SHORT : clôture H1 sous 3 959 (PDL). SL 4 015, objectifs 3 900 puis 3 847.
- Scénario LONG : clôture H1 au-dessus de 4 115 (PDH). SL 3 970, objectifs 4 250 puis 4 405.
- DXY (Dollar Index) : corrélation inverse — si DXY monte, Or baisse.
- US10Y (rendement 10 ans US) : corrélation inverse — rendements en hausse = pression sur l'Or.
"""

gemini_client = genai.Client(api_key=GEMINI_API_KEY)


# ---------------------------------------------------------------------------
# État persistant
# ---------------------------------------------------------------------------

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_price": None, "fired": [], "last_analysis_ts": 0, "last_daily_ts": 0}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state))


# ---------------------------------------------------------------------------
# Données de marché — Yahoo Finance (GC=F = Gold Futures ≈ XAUUSD spot)
# ---------------------------------------------------------------------------

_YF_HEADERS = {"User-Agent": "Mozilla/5.0"}


def get_gold_price():
    resp = requests.get(
        "https://query1.finance.yahoo.com/v8/finance/chart/GC=F",
        params={"interval": "1m", "range": "1d"},
        headers=_YF_HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    closes = resp.json()["chart"]["result"][0]["indicators"]["quote"][0]["close"]
    return float(next(p for p in reversed(closes) if p is not None))


def get_hourly_candles(hours=48):
    days = max(2, hours // 24 + 1)
    resp = requests.get(
        "https://query1.finance.yahoo.com/v8/finance/chart/GC=F",
        params={"interval": "1h", "range": f"{days}d"},
        headers=_YF_HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    ts_list = result["timestamp"]
    q = result["indicators"]["quote"][0]
    candles = [
        {
            "time": ts,
            "open": q["open"][i],
            "high": q["high"][i],
            "low": q["low"][i],
            "close": q["close"][i],
        }
        for i, ts in enumerate(ts_list)
        if q["close"][i] is not None
    ]
    return candles[-hours:]


def get_daily_candles(days=7):
    resp = requests.get(
        "https://query1.finance.yahoo.com/v8/finance/chart/GC=F",
        params={"interval": "1d", "range": f"{days + 2}d"},
        headers=_YF_HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    ts_list = result["timestamp"]
    q = result["indicators"]["quote"][0]
    candles = [
        {
            "date": datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d"),
            "open": q["open"][i],
            "high": q["high"][i],
            "low": q["low"][i],
            "close": q["close"][i],
        }
        for i, ts in enumerate(ts_list)
        if q["close"][i] is not None
    ]
    return candles[-days:]


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, data={"chat_id": CHANNEL, "text": text}, timeout=10)
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# Logique de croisement de niveau
# ---------------------------------------------------------------------------

def crossed(prev_price, price, level):
    if prev_price is None:
        return False
    if level["direction"] == "below":
        return prev_price >= level["price"] and price < level["price"]
    return prev_price <= level["price"] and price > level["price"]


# ---------------------------------------------------------------------------
# Analyses IA (Gemini)
# ---------------------------------------------------------------------------

def generate_hourly_analysis():
    candles = get_hourly_candles(48)
    candles_text = "\n".join(
        f"O={c['open']:.0f} H={c['high']:.0f} L={c['low']:.0f} C={c['close']:.0f}"
        for c in candles[-12:]
    )
    prompt = f"""{STRATEGY_CONTEXT}

Voici les 12 dernières bougies H1 de l'Or (les plus récentes en dernier) :
{candles_text}

Rédige une mise à jour courte (max 100 mots) pour un canal Telegram de trading, en français :
- Ce qui s'est passé sur la dernière heure
- Quel scénario (LONG ou SHORT) semble le plus proche de se confirmer
- Quoi surveiller ensuite
Termine toujours par : "Analyse technique automatisée, pas un conseil financier."
"""
    response = gemini_client.models.generate_content(model=ANALYSIS_MODEL, contents=prompt)
    return response.text


def generate_daily_levels():
    candles = get_daily_candles(7)
    candles_text = "\n".join(
        f"{c['date']}: O={c['open']:.0f} H={c['high']:.0f} L={c['low']:.0f} C={c['close']:.0f}"
        for c in candles
    )
    prompt = f"""Tu es un analyste senior spécialisé dans le trading de l'Or (XAUUSD).

Voici les 7 dernières bougies journalières :
{candles_text}

Identifie les niveaux clés à surveiller aujourd'hui :
1. Résistances majeures (prix plafond à surveiller)
2. Supports majeurs (prix plancher à surveiller)
3. Biais directionnel du jour (haussier / baissier / neutre) — 1 phrase d'explication
4. Niveau de confirmation LONG et SHORT du jour

Réponds en français, format compact (max 150 mots) :
📊 NIVEAUX DU JOUR — Or/XAUUSD
Résistances : [liste]
Supports : [liste]
Biais : [direction + explication]
✅ Confirmation LONG > [prix] | ⛔ SHORT < [prix]

Termine par : "Analyse automatisée, pas un conseil financier."
"""
    response = gemini_client.models.generate_content(model=ANALYSIS_MODEL, contents=prompt)
    return response.text


# ---------------------------------------------------------------------------
# Boucle principale
# ---------------------------------------------------------------------------

def main():
    state = load_state()
    state.setdefault("last_analysis_ts", 0)
    state.setdefault("last_daily_ts", 0)
    print(f"Bot démarré — Or/XAUUSD. Dernier prix : {state['last_price']}, niveaux déclenchés : {state['fired']}")

    while True:
        try:
            price = get_gold_price()
            prev_price = state["last_price"]

            for level in LEVELS:
                key = str(level["price"]) + level["direction"]
                if key in state["fired"]:
                    continue
                if crossed(prev_price, price, level):
                    send_telegram_message(level["message"] + f"\n\nOr actuel : {price:,.0f} $")
                    state["fired"].append(key)
                    print(f"Signal envoyé : {level['message']}")

            state["last_price"] = price
            now = time.time()

            if now - state["last_analysis_ts"] >= ANALYSIS_INTERVAL_SECONDS:
                analysis = generate_hourly_analysis()
                send_telegram_message(f"🤖 Analyse horaire — Or/XAUUSD\n\n{analysis}")
                state["last_analysis_ts"] = now
                print("Analyse horaire envoyée.")

            if now - state["last_daily_ts"] >= DAILY_LEVELS_INTERVAL_SECONDS:
                daily = generate_daily_levels()
                send_telegram_message(f"📅 {daily}")
                state["last_daily_ts"] = now
                print("Niveaux du jour envoyés.")

            save_state(state)

        except Exception as exc:
            print(f"Erreur : {exc}")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
