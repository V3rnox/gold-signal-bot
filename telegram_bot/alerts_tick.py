"""
Vérification des niveaux — toutes les 5 minutes.
Envoie un message de signal uniquement quand TOUS les filtres sont alignés.
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


def signal_message(level, spot, rsi, h4_bias, dxy, ema50):
    direction = "SHORT ⬇️" if level["direction"] == "below" else "LONG ⬆️"
    niveau_txt = f"clôture H1 {'sous' if level['direction'] == 'below' else 'au-dessus de'} {level['price']:,} $"

    if level["direction"] == "below":
        sl = 4015
        tp1 = 3900
        tp2 = 3847
        entry = level["price"] - 10
        rr = round((entry - tp1) / (sl - entry), 1)
    else:
        sl = 3970
        tp1 = 4250
        tp2 = 4405
        entry = level["price"] + 10
        rr = round((tp1 - entry) / (entry - sl), 1)

    confluences = []
    if h4_bias:
        confluences.append(f"Structure H4 {h4_bias}")
    if rsi:
        confluences.append(f"RSI {rsi} (pas d'excès)")
    if ema50:
        ema_ok = spot < ema50 if level["direction"] == "below" else spot > ema50
        confluences.append(f"Prix {'sous' if spot < ema50 else 'au-dessus de'} EMA50 ({ema50:.0f} $)")
    if dxy:
        confluences.append(f"DXY {dxy:.2f}")

    confluences_txt = "\n".join(f"✅ {c}" for c in confluences)

    return (
        f"🚨 *SIGNAL Or/XAUUSD* 🚨\n\n"
        f"Direction : *{direction}*\n"
        f"Déclencheur : {niveau_txt}\n\n"
        f"*Confluence :*\n{confluences_txt}\n\n"
        f"*Gestion du trade :*\n"
        f"▸ Zone d'entrée : ~{entry:,.0f} $\n"
        f"▸ Stop Loss : {sl:,} $\n"
        f"▸ TP1 : {tp1:,} $ (sortie partielle)\n"
        f"▸ TP2 : {tp2:,} $ (sortie totale)\n"
        f"▸ R:R estimé : {rr}\n\n"
        f"_Chacun gère son propre capital.\n"
        f"Pas un conseil financier._"
    )


def filtered_message(level, spot, blocks):
    direction = "SHORT" if level["direction"] == "below" else "LONG"
    return (
        f"👁 *Niveau cassé — signal filtré*\n\n"
        f"Direction potentielle : {direction}\n"
        f"Niveau : {level['price']:,} $ ({level['direction']})\n\n"
        f"Filtres bloquants :\n" +
        "\n".join(f"⛔ {b}" for b in blocks) +
        f"\n\nOr : {spot:,.0f} $ — attendre meilleure confluence."
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
    spot = get_gold_price()

    last_closed = get_last_closed_h1(candles_h1)
    current_h1_close = last_closed["close"] if last_closed else None
    current_h1_ts = last_closed["time"] if last_closed else 0

    print(f"Or: {spot:.0f}$ | RSI: {rsi} | EMA50: {ema50} | H4: {h4_bias} | Session: {in_trading_session()}")

    if current_h1_ts != state["last_h1_ts"] and current_h1_close is not None:
        prev_h1_close = state["last_h1_close"]

        for level in LEVELS:
            key = str(level["price"]) + level["direction"]
            if key in state["fired"]:
                continue
            if not h1_crossed(prev_h1_close, current_h1_close, level):
                continue

            blocks = []
            if not in_trading_session():
                blocks.append("Session asiatique — liquidité faible")
            if not rsi_filter_ok(rsi, level):
                blocks.append(f"RSI {rsi} (excès — attendre normalisation)")
            if not ema_filter_ok(spot, ema50, level):
                blocks.append(f"Prix contre EMA50 ({ema50:.0f} $)")
            if not h4_bias_filter_ok(h4_bias, level):
                blocks.append(f"Biais H4 {h4_bias} — contre le signal")

            if blocks:
                send_telegram_message(filtered_message(level, spot, blocks))
                print(f"Signal filtré : {level['price']} — {blocks}")
            else:
                send_telegram_message(signal_message(level, spot, rsi, h4_bias, dxy, ema50))
                state["fired"].append(key)
                print(f"✅ Signal envoyé : {level['price']}")

        state["last_h1_close"] = current_h1_close
        state["last_h1_ts"] = current_h1_ts

    save_state(state)
    print("Vérification terminée.")


if __name__ == "__main__":
    run()
