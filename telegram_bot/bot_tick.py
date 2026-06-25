"""
One-shot execution — appelé par GitHub Actions toutes les 5 minutes.
Charge l'état, vérifie les prix, applique tous les filtres, envoie les alertes, sauvegarde.
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
    get_h4_candles,
    get_rsi,
    get_ema,
    get_h4_bias,
    get_fibonacci_levels,
    get_dxy_price,
    get_last_closed_h1,
    h1_crossed,
    rsi_filter_ok,
    ema_filter_ok,
    h4_bias_filter_ok,
    in_trading_session,
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

    # --- Données de marché ---
    candles_h1 = get_hourly_candles(60)
    h4_candles = get_h4_candles(30)
    rsi = get_rsi(candles_h1)
    ema50 = get_ema(candles_h1, 50)
    h4_bias = get_h4_bias(h4_candles)
    fib = get_fibonacci_levels(candles_h1, lookback=50)
    dxy = get_dxy_price()
    spot_price = get_gold_price()

    last_closed = get_last_closed_h1(candles_h1)
    current_h1_close = last_closed["close"] if last_closed else None
    current_h1_ts = last_closed["time"] if last_closed else 0
    prev_h1_close = state["last_h1_close"]

    print(f"Or: {spot_price:.0f}$ | RSI: {rsi} | EMA50: {ema50} | H4: {h4_bias} | DXY: {dxy} | Session: {in_trading_session()}")

    # --- Vérification des niveaux sur nouvelle bougie H1 clôturée ---
    if current_h1_ts != state["last_h1_ts"] and current_h1_close is not None:
        session_ok = in_trading_session()

        for level in LEVELS:
            key = str(level["price"]) + level["direction"]
            if key in state["fired"]:
                continue
            if not h1_crossed(prev_h1_close, current_h1_close, level):
                continue

            # Collecter les filtres qui bloquent
            blocks = []
            if not session_ok:
                blocks.append("session asiatique (faible liquidité)")
            if not rsi_filter_ok(rsi, level):
                blocks.append(f"RSI {rsi} défavorable")
            if not ema_filter_ok(spot_price, ema50, level):
                direction_word = "dessus" if level["direction"] == "above" else "dessous"
                blocks.append(f"prix {('sous' if level['direction'] == 'above' else 'au-dessus de')} EMA50 ({ema50:.0f})")
            if not h4_bias_filter_ok(h4_bias, level):
                blocks.append(f"biais H4 {h4_bias} contraire")

            if blocks:
                # Signal détecté mais filtré — on prévient sans déclencher
                send_telegram_message(
                    f"⚠️ Signal H1 détecté — filtré\n"
                    f"Niveau : {level['price']} $ ({level['direction']})\n"
                    f"Raisons : {', '.join(blocks)}\n\n"
                    f"Or actuel : {spot_price:,.0f} $"
                )
                print(f"Signal filtré ({', '.join(blocks)}) : {level['price']}")
                continue

            # Signal valide — tous les filtres passent
            extras = []
            if dxy:
                extras.append(f"DXY : {dxy:.2f}")
            if rsi:
                extras.append(f"RSI : {rsi}")
            if h4_bias:
                extras.append(f"H4 : {h4_bias}")
            extras_str = " | ".join(extras)

            send_telegram_message(
                level["message"]
                + f"\n\n✅ Confluence : {extras_str}"
                + f"\nOr : {spot_price:,.0f} $"
            )
            state["fired"].append(key)
            print(f"Signal envoyé : {level['message']}")

        state["last_h1_close"] = current_h1_close
        state["last_h1_ts"] = current_h1_ts

    now = time.time()

    # --- Analyse horaire ---
    if now - state["last_analysis_ts"] >= ANALYSIS_INTERVAL_SECONDS:
        try:
            analysis = generate_hourly_analysis(rsi, dxy, ema50, h4_bias, fib)
            rsi_line = f" | RSI : {rsi}" if rsi else ""
            h4_line = f" | H4 : {h4_bias}" if h4_bias else ""
            send_telegram_message(
                f"🤖 Analyse horaire — Or/XAUUSD\n"
                f"Prix : {spot_price:,.0f} ${rsi_line}{h4_line}\n\n{analysis}"
            )
            state["last_analysis_ts"] = now
            print("Analyse horaire envoyée.")
        except Exception as e:
            print(f"Analyse horaire ignorée (Gemini indisponible) : {e}")

    # --- Niveaux du jour ---
    if now - state["last_daily_ts"] >= DAILY_LEVELS_INTERVAL_SECONDS:
        try:
            daily = generate_daily_levels()
            send_telegram_message(f"📅 {daily}")
            state["last_daily_ts"] = now
            print("Niveaux du jour envoyés.")
        except Exception as e:
            print(f"Niveaux du jour ignorés (Gemini indisponible) : {e}")

    save_state(state)
    print("Tick terminé.")


if __name__ == "__main__":
    run_tick()
