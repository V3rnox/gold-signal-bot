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
            "🟠 Or/XAUUSD — Confirmation SHORT\n"
            "Clôture H1 sous 3 959 $ (PDL)\n"
            "SL : 4 015 $ | Objectif 1 : 3 900 $ | Objectif 2 : 3 847 $\n"
            "R:R minimum 2:1"
        ),
        "rsi_filter": "above_30",  # pas de SHORT si RSI < 30 (survente)
    },
    {
        "price": 4115,
        "direction": "above",
        "message": (
            "🔵 Or/XAUUSD — Confirmation LONG\n"
            "Clôture H1 au-dessus de 4 115 $ (PDH)\n"
            "SL : 3 970 $ | Objectif 1 : 4 250 $ | Objectif 2 : 4 405 $\n"
            "R:R minimum 2:1"
        ),
        "rsi_filter": "below_70",  # pas de LONG si RSI > 70 (surachat)
    },
    {
        "price": 4250,
        "direction": "above",
        "message": "🎯 Objectif LONG 1 atteint : 4 250 $\nEnvisager prise de bénéfices partielle.",
        "rsi_filter": None,
    },
    {
        "price": 4405,
        "direction": "above",
        "message": "🎯 Objectif LONG 2 atteint : 4 405 $\nObjectif final — prise de bénéfices totale.",
        "rsi_filter": None,
    },
    {
        "price": 3900,
        "direction": "below",
        "message": "🎯 Objectif SHORT 1 atteint : 3 900 $\nEnvisager prise de bénéfices partielle.",
        "rsi_filter": None,
    },
    {
        "price": 3847,
        "direction": "below",
        "message": "🎯 Objectif SHORT 2 atteint : 3 847 $\nObjectif final — prise de bénéfices totale.",
        "rsi_filter": None,
    },
]

STRATEGY_CONTEXT = """
Contexte stratégie Or/XAUUSD (SMC + intermarché) :
- Structure : BOS baissier depuis le sommet 4 428, cassures successives vers le bas.
- PDH (hier haut) = 4 115 | PDL (hier bas) = 3 959. Zone neutre actuelle ~3 988.
- Scénario SHORT : clôture H1 sous 3 959 (PDL). SL 4 015, objectifs 3 900 puis 3 847.
- Scénario LONG : clôture H1 au-dessus de 4 115 (PDH). SL 3 970, objectifs 4 250 puis 4 405.
- Clés de confirmation supplémentaires :
  * RSI(14) H1 : éviter LONG si RSI > 70, éviter SHORT si RSI < 30
  * DXY (Dollar Index) : corrélation inverse — DXY monte = Or baisse
  * US10Y (rendement 10 ans US) : corrélation inverse — rendements montent = pression sur l'Or
  * Volume : une cassure avec fort volume augmente la fiabilité
"""

gemini_client = genai.Client(api_key=GEMINI_API_KEY)
_YF_HEADERS = {"User-Agent": "Mozilla/5.0"}


# ---------------------------------------------------------------------------
# État persistant
# ---------------------------------------------------------------------------

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {
        "last_h1_close": None,
        "last_h1_ts": 0,
        "fired": [],
        "last_analysis_ts": 0,
        "last_daily_ts": 0,
    }


def save_state(state):
    STATE_FILE.write_text(json.dumps(state))


# ---------------------------------------------------------------------------
# Données de marché — Yahoo Finance (GC=F = Gold Futures ≈ XAUUSD spot)
# ---------------------------------------------------------------------------

def _yf_chart(symbol, interval, period):
    resp = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        params={"interval": interval, "range": period},
        headers=_YF_HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["chart"]["result"][0]


def get_gold_price():
    """Prix spot Or via dernière valeur 1m."""
    result = _yf_chart("GC=F", "1m", "1d")
    closes = result["indicators"]["quote"][0]["close"]
    return float(next(p for p in reversed(closes) if p is not None))


def get_hourly_candles(hours=48):
    """Bougies H1 Gold."""
    days = max(2, hours // 24 + 1)
    result = _yf_chart("GC=F", "1h", f"{days}d")
    ts_list = result["timestamp"]
    q = result["indicators"]["quote"][0]
    candles = [
        {"time": ts_list[i], "open": q["open"][i], "high": q["high"][i],
         "low": q["low"][i], "close": q["close"][i]}
        for i in range(len(ts_list))
        if q["close"][i] is not None
    ]
    return candles[-hours:]


def get_daily_candles(days=7):
    """Bougies journalières Gold."""
    result = _yf_chart("GC=F", "1d", f"{days + 2}d")
    ts_list = result["timestamp"]
    q = result["indicators"]["quote"][0]
    candles = [
        {
            "date": datetime.utcfromtimestamp(ts_list[i]).strftime("%Y-%m-%d"),
            "open": q["open"][i], "high": q["high"][i],
            "low": q["low"][i], "close": q["close"][i],
        }
        for i in range(len(ts_list))
        if q["close"][i] is not None
    ]
    return candles[-days:]


def get_rsi(candles, period=14):
    """RSI(14) calculé sur les clôtures H1."""
    closes = [c["close"] for c in candles]
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def get_dxy_price():
    """Prix DXY (Dollar Index) — corrélation inverse avec l'Or."""
    try:
        result = _yf_chart("DX-Y.NYB", "1m", "1d")
        closes = result["indicators"]["quote"][0]["close"]
        return float(next(p for p in reversed(closes) if p is not None))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, data={"chat_id": CHANNEL, "text": text}, timeout=10)
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# Logique SMC : confirmation sur clôture H1
# ---------------------------------------------------------------------------

def get_last_closed_h1(candles):
    """Retourne l'avant-dernière bougie (la dernière clôturée)."""
    # La dernière bougie est en cours de formation, on prend -2
    if len(candles) >= 2:
        return candles[-2]
    return None


def h1_crossed(prev_close, current_close, level):
    """Vérifie si la clôture H1 vient de franchir le niveau."""
    if prev_close is None or current_close is None:
        return False
    if level["direction"] == "below":
        return prev_close >= level["price"] and current_close < level["price"]
    return prev_close <= level["price"] and current_close > level["price"]


def rsi_filter_ok(rsi, level):
    """Retourne False si le RSI invalide le signal."""
    if rsi is None or level["rsi_filter"] is None:
        return True
    if level["rsi_filter"] == "below_70" and rsi > 70:
        return False
    if level["rsi_filter"] == "above_30" and rsi < 30:
        return False
    return True


# ---------------------------------------------------------------------------
# Analyses IA (Gemini)
# ---------------------------------------------------------------------------

def generate_hourly_analysis(rsi, dxy):
    candles = get_hourly_candles(48)
    candles_text = "\n".join(
        f"O={c['open']:.0f} H={c['high']:.0f} L={c['low']:.0f} C={c['close']:.0f}"
        for c in candles[-12:]
    )
    intermarket = ""
    if dxy is not None:
        intermarket = f"- DXY actuel : {dxy:.2f} (si DXY monte → pression baissière sur l'Or)\n"
    if rsi is not None:
        intermarket += f"- RSI(14) H1 actuel : {rsi} (> 70 = surachat, < 30 = survente)\n"

    prompt = f"""{STRATEGY_CONTEXT}

Données intermarché actuelles :
{intermarket if intermarket else '(non disponibles)'}

Dernières 12 bougies H1 de l'Or (les plus récentes en dernier) :
{candles_text}

Rédige une mise à jour courte (max 120 mots) pour un canal Telegram de trading, en français :
- Ce qui s'est passé sur la dernière heure
- Niveau RSI et ce que ça implique (surachat / survente / neutre)
- Impact du DXY sur le biais Or
- Quel scénario (LONG ou SHORT) semble le plus proche de se confirmer et pourquoi
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
    prompt = f"""Tu es un analyste senior spécialisé dans le trading de l'Or (XAUUSD) avec une expertise en SMC, VSA et intermarché.

Voici les 7 dernières bougies journalières :
{candles_text}

Analyse ces données et identifie :
1. Les résistances majeures (prix plafond à surveiller aujourd'hui)
2. Les supports majeurs (prix plancher à surveiller aujourd'hui)
3. Le biais directionnel du jour (haussier / baissier / neutre) avec 1 phrase d'explication basée sur la structure
4. Le niveau de confirmation LONG et SHORT du jour (clôture H1 requise)
5. Un signal VSA potentiel : est-ce qu'une des bougies récentes montre un volume anormal, un spread étroit sur fort volume (No Supply / No Demand), ou une bougie de test ? (optionnel si pertinent)

Réponds en français, format compact (max 160 mots) :
📊 NIVEAUX DU JOUR — Or/XAUUSD
Résistances : [liste]
Supports : [liste]
Biais : [direction + explication]
✅ Confirmation LONG > [prix] | ⛔ SHORT < [prix]
📈 VSA : [signal si pertinent, sinon "RAS"]

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
    state.setdefault("last_h1_close", None)
    state.setdefault("last_h1_ts", 0)
    print(f"Bot démarré — Or/XAUUSD. Niveaux déjà déclenchés : {state['fired']}")

    while True:
        try:
            candles_h1 = get_hourly_candles(50)
            rsi = get_rsi(candles_h1)
            dxy = get_dxy_price()
            spot_price = get_gold_price()

            last_closed = get_last_closed_h1(candles_h1)
            current_h1_close = last_closed["close"] if last_closed else None
            current_h1_ts = last_closed["time"] if last_closed else 0

            prev_h1_close = state["last_h1_close"]

            # Vérification des niveaux uniquement sur une nouvelle bougie H1 clôturée
            if current_h1_ts != state["last_h1_ts"] and current_h1_close is not None:
                for level in LEVELS:
                    key = str(level["price"]) + level["direction"]
                    if key in state["fired"]:
                        continue
                    if h1_crossed(prev_h1_close, current_h1_close, level):
                        if not rsi_filter_ok(rsi, level):
                            rsi_msg = f"\n⚠️ Signal filtré par RSI ({rsi}) — attendre meilleure confluence."
                            send_telegram_message(
                                f"👁 Signal H1 détecté mais RSI défavorable\n"
                                f"Niveau : {level['price']} $ ({level['direction']})"
                                f"{rsi_msg}\n\nOr actuel : {spot_price:,.0f} $"
                            )
                            print(f"Signal filtré par RSI: niveau {level['price']}, RSI={rsi}")
                            continue
                        # Signal valide
                        dxy_note = f"\nDXY : {dxy:.2f}" if dxy else ""
                        rsi_note = f" | RSI : {rsi}" if rsi else ""
                        send_telegram_message(
                            level["message"]
                            + f"\n\nOr actuel : {spot_price:,.0f} ${dxy_note}{rsi_note}"
                        )
                        state["fired"].append(key)
                        print(f"Signal envoyé : {level['message']}")

                state["last_h1_close"] = current_h1_close
                state["last_h1_ts"] = current_h1_ts

            now = time.time()

            if now - state["last_analysis_ts"] >= ANALYSIS_INTERVAL_SECONDS:
                analysis = generate_hourly_analysis(rsi, dxy)
                rsi_line = f" | RSI : {rsi}" if rsi else ""
                dxy_line = f" | DXY : {dxy:.2f}" if dxy else ""
                send_telegram_message(
                    f"🤖 Analyse horaire — Or/XAUUSD\n"
                    f"Prix : {spot_price:,.0f} ${rsi_line}{dxy_line}\n\n{analysis}"
                )
                state["last_analysis_ts"] = now
                print(f"Analyse horaire envoyée. RSI={rsi}, DXY={dxy}")

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
