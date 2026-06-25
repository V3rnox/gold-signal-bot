"""
Analyse horaire — publiée toutes les heures sur le canal.
Format structuré : structure, indicateurs, scénarios, confluence, à surveiller.
"""
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))

from bot import (
    get_gold_price, get_hourly_candles, get_h4_candles, get_daily_candles,
    get_rsi, get_ema, get_h4_bias, get_fibonacci_levels, get_dxy_price,
    get_gemini_client, ANALYSIS_MODEL,
    send_telegram_message,
)


def build_prompt(spot, rsi, ema50, h4_bias, fib, dxy, h1_candles, h4_candles):
    h1_recent = "\n".join(
        f"  {datetime.utcfromtimestamp(c['time']).strftime('%H:%M')} "
        f"O={c['open']:.0f} H={c['high']:.0f} L={c['low']:.0f} C={c['close']:.0f}"
        for c in h1_candles[-8:]
    )
    h4_recent = "\n".join(
        f"  O={c['open']:.0f} H={c['high']:.0f} L={c['low']:.0f} C={c['close']:.0f}"
        for c in h4_candles[-4:]
    )
    ema_pos = "AU-DESSUS" if ema50 and spot > ema50 else "EN-DESSOUS"
    fib_block = ""
    if fib:
        fib_block = f"Fibonacci : 0.382={fib['fib_382']:.0f} | 0.500={fib['fib_500']:.0f} | 0.618={fib['fib_618']:.0f} | 0.786={fib['fib_786']:.0f}"

    return f"""Tu es un analyste senior en trading de l'Or (XAUUSD), expert SMC et intermarché.
Tu gères un canal Telegram de trading. Chaque heure tu publies une analyse claire, utile et pédagogique.

DONNÉES ACTUELLES :
- Prix : {spot:,.0f} $
- Heure UTC : {datetime.utcnow().strftime('%Hh%M')}
- RSI(14) H1 : {rsi if rsi else 'N/D'}
- EMA50 H1 : {f'{ema50:.0f} $ (prix {ema_pos})' if ema50 else 'N/D'}
- Biais H4 : {h4_bias if h4_bias else 'N/D'}
- DXY : {f'{dxy:.2f}' if dxy else 'N/D'}
- {fib_block}

Niveaux SMC clés :
- PDH (résistance) = 4 115 $ → LONG confirmé si clôture H1 au-dessus
- PDL (support) = 3 959 $ → SHORT confirmé si clôture H1 en-dessous
- CHoCH haussier récent @ 4 019 $ (retournement local)
- EQL balayées @ 3 974 $ (liquidité prise)
- EQH résistance @ 4 002 $
- SL LONG = 3 970 $ | SL SHORT = 4 015 $
- TP LONG : 4 250 $ puis 4 405 $
- TP SHORT : 3 900 $ puis 3 847 $

Dernières H1 :
{h1_recent}

Dernières H4 :
{h4_recent}

INSTRUCTIONS — Rédige l'analyse en respectant EXACTEMENT ce format (en français) :

🪙 *Or/XAUUSD — [HEURE]h UTC*

📍 *Structure de marché*
[2-3 phrases : tendance H4, dernier BOS/CHoCH significatif, où en est le prix par rapport aux niveaux clés]

📊 *Indicateurs*
▸ RSI : [valeur + interprétation courte]
▸ EMA50 : [valeur + position du prix + implication]
▸ DXY : [valeur + impact sur l'Or]

🎯 *Scénarios*
🔵 LONG — clôture H1 > 4 115 $
   SL : 3 970 $ | TP1 : 4 250 $ | TP2 : 4 405 $
   [1 phrase sur les conditions favorables ou défavorables au LONG]

🟠 SHORT — clôture H1 < 3 959 $
   SL : 4 015 $ | TP1 : 3 900 $ | TP2 : 3 847 $
   [1 phrase sur les conditions favorables ou défavorables au SHORT]

⚡ *Confluence*
[2-3 phrases : quels indicateurs s'alignent ? quel scénario a la meilleure confluence ? niveau Fibonacci proche ?]

👁 *À surveiller cette heure*
[1-2 éléments concrets et précis à observer sur le graphique]

_Analyse automatisée — pas un conseil financier._

Règles :
- Max 220 mots
- Précis, concret, pas de formules vagues
- Toujours mentionner les deux scénarios avec leurs niveaux
- Ne jamais inventer des données non fournies
"""


def fallback_message(spot, rsi, ema50, h4_bias, dxy, fib):
    ema_pos = "au-dessus" if ema50 and spot > ema50 else "en-dessous"
    fib_line = f"\n▸ Fib 0.618 : {fib['fib_618']:.0f} $ | 0.786 : {fib['fib_786']:.0f} $" if fib else ""
    return (
        f"📊 *Or/XAUUSD — {datetime.utcnow().strftime('%Hh')} UTC*\n\n"
        f"▸ Prix : {spot:,.0f} $\n"
        f"▸ RSI(14) : {rsi} | EMA50 : {ema50:.0f} $ (prix {ema_pos})\n"
        f"▸ Biais H4 : {h4_bias} | DXY : {dxy:.2f}{fib_line}\n\n"
        f"🎯 *Scénarios*\n"
        f"🔵 LONG — clôture H1 > 4 115 $\n"
        f"   SL : 3 970 $ | TP1 : 4 250 $ | TP2 : 4 405 $\n\n"
        f"🟠 SHORT — clôture H1 < 3 959 $\n"
        f"   SL : 4 015 $ | TP1 : 3 900 $ | TP2 : 3 847 $\n\n"
        f"_Analyse IA indisponible — données brutes._"
    )


def run():
    candles_h1 = get_hourly_candles(60)
    h4_candles = get_h4_candles(30)
    rsi = get_rsi(candles_h1)
    ema50 = get_ema(candles_h1, 50)
    h4_bias = get_h4_bias(h4_candles)
    fib = get_fibonacci_levels(candles_h1, 50)
    dxy = get_dxy_price()
    spot = get_gold_price()

    try:
        prompt = build_prompt(spot, rsi, ema50, h4_bias, fib, dxy, candles_h1, h4_candles)
        response = get_gemini_client().models.generate_content(model=ANALYSIS_MODEL, contents=prompt)
        message = response.text
        print("Analyse Gemini générée.")
    except Exception as e:
        message = fallback_message(spot, rsi, ema50, h4_bias, dxy, fib)
        print(f"Fallback (Gemini KO : {e})")

    send_telegram_message(message)
    print(f"Message envoyé — Or : {spot:.0f}$")


if __name__ == "__main__":
    run()
