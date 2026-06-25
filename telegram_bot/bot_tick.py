"""
One-shot execution — appelé par GitHub Actions toutes les 5 minutes.
Charge l'état, vérifie les prix, envoie les alertes, sauvegarde l'état.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bot import (
    LEVELS,
    ANALYSIS_INTERVAL_SECONDS,
    DAILY_LEVELS_INTERVAL_SECONDS,
    load_state,
    save_state,
    get_gold_price,
    get_hourly_candles,
    get_rsi,
    get_dxy_price,
    get_last_closed_h1,
    h1_crossed,
    rsi_filter_ok,
    generate_hourly_analysis,
    generate_daily_levels,
    send_telegram_message,
)


def run_tick():
    state = load_state()
    state.setdefault("last_analysis_ts", 0)
    state.setdefault("last_daily_ts", 0)
    state.setdefault("last_h1_close", None)
    state.setdefault("last_h1_ts", 0)

    candles_h1 = get_hourly_candles(50)
    rsi = get_rsi(candles_h1)
    dxy = get_dxy_price()
    spot_price = get_gold_price()

    last_closed = get_last_closed_h1(candles_h1)
    current_h1_close = last_closed["close"] if last_closed else None
    current_h1_ts = last_closed["time"] if last_closed else 0

    prev_h1_close = state["last_h1_close"]

    # Vérification des niveaux sur nouvelle bougie H1 clôturée
    if current_h1_ts != state["last_h1_ts"] and current_h1_close is not None:
        for level in LEVELS:
            key = str(level["price"]) + level["direction"]
            if key in state["fired"]:
                continue
            if h1_crossed(prev_h1_close, current_h1_close, level):
                if not rsi_filter_ok(rsi, level):
                    send_telegram_message(
                        f"👁 Signal H1 détecté mais RSI défavorable ({rsi})\n"
                        f"Niveau : {level['price']} $ ({level['direction']})\n"
                        f"Attendre meilleure confluence.\n\nOr actuel : {spot_price:,.0f} $"
                    )
                    print(f"Signal filtré RSI : niveau {level['price']}, RSI={rsi}")
                    continue
                dxy_note = f"\nDXY : {dxy:.2f}" if dxy else ""
                rsi_note = f" | RSI : {rsi}" if rsi else ""
                send_telegram_message(
                    level["message"] + f"\n\nOr actuel : {spot_price:,.0f} ${dxy_note}{rsi_note}"
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
    print(f"Tick OK. Or : {spot_price:.0f} $ | RSI : {rsi} | DXY : {dxy}")


if __name__ == "__main__":
    run_tick()
