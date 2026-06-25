"""
Analyse horaire — se déclenche une fois par heure via cron GitHub Actions.
Envoie toujours un message : analyse Gemini si dispo, sinon statut du marché.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bot import (
    get_gold_price, get_hourly_candles, get_h4_candles,
    get_rsi, get_ema, get_h4_bias, get_fibonacci_levels, get_dxy_price,
    generate_hourly_analysis, send_telegram_message,
)


def run():
    candles_h1 = get_hourly_candles(60)
    h4_candles = get_h4_candles(30)
    rsi = get_rsi(candles_h1)
    ema50 = get_ema(candles_h1, 50)
    h4_bias = get_h4_bias(h4_candles)
    fib = get_fibonacci_levels(candles_h1, 50)
    dxy = get_dxy_price()
    spot_price = get_gold_price()

    # Ligne de statut toujours affichée
    parts = [f"Prix : {spot_price:,.0f} $"]
    if rsi:
        parts.append(f"RSI : {rsi}")
    if ema50:
        parts.append(f"EMA50 : {ema50:.0f}")
    if h4_bias:
        parts.append(f"H4 : {h4_bias}")
    if dxy:
        parts.append(f"DXY : {dxy:.2f}")
    status_line = " | ".join(parts)

    fib_line = ""
    if fib:
        fib_line = (
            f"\nFib (swing {fib['high']:.0f}→{fib['low']:.0f}) : "
            f"0.618={fib['fib_618']:.0f} | 0.500={fib['fib_500']:.0f} | 0.786={fib['fib_786']:.0f}"
        )

    try:
        analysis = generate_hourly_analysis(rsi, dxy, ema50, h4_bias, fib)
        message = (
            f"🤖 Analyse horaire — Or/XAUUSD\n"
            f"{status_line}{fib_line}\n\n"
            f"{analysis}"
        )
        print("Analyse Gemini OK")
    except Exception as e:
        message = (
            f"📊 Statut horaire — Or/XAUUSD\n"
            f"{status_line}{fib_line}\n\n"
            f"Niveaux clés :\n"
            f"⛔ SHORT < 3 959 $ | ✅ LONG > 4 115 $\n"
            f"SL SHORT : 4 015 $ | SL LONG : 3 970 $\n\n"
            f"Analyse IA indisponible (réessai à la prochaine heure)."
        )
        print(f"Gemini KO ({e}) — statut envoyé")

    send_telegram_message(message)
    print("Message envoyé.")


if __name__ == "__main__":
    run()
