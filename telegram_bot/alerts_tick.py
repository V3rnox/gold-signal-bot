"""
Vérification des niveaux — toutes les 5 minutes.
Envoie une alerte uniquement si un niveau de confirmation est cassé avec confluence.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bot import (
    LEVELS,
    load_state, save_state,
    get_gold_price, get_hourly_candles, get_h4_candles,
    get_rsi, get_ema, get_h4_bias, get_dxy_price,
    get_last_closed_h1, h1_crossed,
    rsi_filter_ok, ema_filter_ok, h4_bias_filter_ok, in_trading_session,
    send_telegram_message,
)


def run():
    state = load_state()
    state.setdefault("fired", [])
    state.setdefault("last_h1_close", None)
    state.setdefault("last_h1_ts", 0)
    state.setdefault("last_analysis_ts", 0)
    state.setdefault("last_daily_ts", 0)

    candles_h1 = get_hourly_candles(60)
    h4_candles = get_h4_candles(30)
    rsi = get_rsi(candles_h1)
    ema50 = get_ema(candles_h1, 50)
    h4_bias = get_h4_bias(h4_candles)
    dxy = get_dxy_price()
    spot_price = get_gold_price()

    last_closed = get_last_closed_h1(candles_h1)
    current_h1_close = last_closed["close"] if last_closed else None
    current_h1_ts = last_closed["time"] if last_closed else 0

    print(f"Or: {spot_price:.0f}$ | RSI: {rsi} | EMA50: {ema50} | H4: {h4_bias} | Session: {in_trading_session()}")

    if current_h1_ts != state["last_h1_ts"] and current_h1_close is not None:
        session_ok = in_trading_session()
        prev_h1_close = state["last_h1_close"]

        for level in LEVELS:
            key = str(level["price"]) + level["direction"]
            if key in state["fired"]:
                continue
            if not h1_crossed(prev_h1_close, current_h1_close, level):
                continue

            blocks = []
            if not session_ok:
                blocks.append("session asiatique")
            if not rsi_filter_ok(rsi, level):
                blocks.append(f"RSI {rsi} défavorable")
            if not ema_filter_ok(spot_price, ema50, level):
                blocks.append(f"prix contre EMA50 ({ema50:.0f})")
            if not h4_bias_filter_ok(h4_bias, level):
                blocks.append(f"H4 {h4_bias} contraire")

            if blocks:
                send_telegram_message(
                    f"⚠️ Niveau cassé mais filtré\n"
                    f"Niveau : {level['price']} $ ({level['direction']})\n"
                    f"Filtres bloquants : {', '.join(blocks)}\n"
                    f"Or : {spot_price:,.0f} $"
                )
                print(f"Filtré ({', '.join(blocks)}) : {level['price']}")
                continue

            extras = " | ".join(filter(None, [
                f"DXY {dxy:.2f}" if dxy else None,
                f"RSI {rsi}" if rsi else None,
                f"H4 {h4_bias}" if h4_bias else None,
            ]))
            send_telegram_message(
                level["message"]
                + f"\n\n✅ Confluence : {extras}"
                + f"\nOr : {spot_price:,.0f} $"
            )
            state["fired"].append(key)
            print(f"Signal envoyé : {level['price']}")

        state["last_h1_close"] = current_h1_close
        state["last_h1_ts"] = current_h1_ts

    save_state(state)
    print("Vérification terminée.")


if __name__ == "__main__":
    run()
