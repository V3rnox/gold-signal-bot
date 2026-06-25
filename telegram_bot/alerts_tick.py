"""
Surveillance toutes les 5 minutes — système en 3 stades :
1. En approche  : prix à <50$ du niveau
2. Surveillance : prix à <20$ du niveau
3. Signal       : clôture H1 confirmée + tous filtres OK
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

# Distances de déclenchement
APPROACH_DISTANCE = 50   # $ du niveau → alerte "en approche"
WATCH_DISTANCE = 20      # $ du niveau → alerte "surveillance active"


def confluence_score(rsi, ema50, spot, h4_bias, level):
    """Retourne (score, détails) — score sur 4."""
    checks = []
    if in_trading_session():
        checks.append(("✅", "Session active (Londres/NY)"))
    else:
        checks.append(("⛔", "Session asiatique (liquidité faible)"))

    if rsi_filter_ok(rsi, level):
        checks.append(("✅", f"RSI {rsi} — pas d'excès"))
    else:
        checks.append(("⛔", f"RSI {rsi} — excès, attendre"))

    if ema_filter_ok(spot, ema50, level):
        side = "sous" if level["direction"] == "below" else "au-dessus de"
        checks.append(("✅", f"Prix {side} EMA50 ({ema50:.0f} $)"))
    else:
        checks.append(("⛔", f"EMA50 ({ema50:.0f} $) — contre le signal"))

    if h4_bias_filter_ok(h4_bias, level):
        checks.append(("✅", f"Biais H4 {h4_bias} — aligné"))
    else:
        checks.append(("⛔", f"Biais H4 {h4_bias} — contraire"))

    score = sum(1 for icon, _ in checks if icon == "✅")
    return score, checks


def is_approaching(spot, level, distance):
    if level["direction"] == "below":
        return 0 < (spot - level["price"]) <= distance
    else:
        return 0 < (level["price"] - spot) <= distance


def approach_message(level, spot, score, checks, stage):
    direction = "SHORT ⬇️" if level["direction"] == "below" else "LONG ⬆️"
    niveau = level["price"]
    distance = abs(spot - niveau)

    if stage == "watch":
        emoji = "🔴"
        titre = f"SURVEILLANCE ACTIVE — {direction}"
        sous_titre = f"Prix à seulement {distance:.0f} $ du niveau !\nProchaine clôture H1 DÉCISIVE."
    else:
        emoji = "🟡"
        titre = f"En approche — {direction}"
        sous_titre = f"Prix à {distance:.0f} $ du niveau de confirmation."

    confluence_txt = "\n".join(f"{icon} {desc}" for icon, desc in checks)

    if level["direction"] == "below":
        condition = f"clôture H1 sous {niveau:,} $"
        sl = 4015
        tp1 = 3900
        tp2 = 3847
    else:
        condition = f"clôture H1 au-dessus de {niveau:,} $"
        sl = 3970
        tp1 = 4250
        tp2 = 4405

    return (
        f"{emoji} *{titre}*\n\n"
        f"Prix actuel : *{spot:,.0f} $*\n"
        f"{sous_titre}\n\n"
        f"*Confluence ({score}/4) :*\n{confluence_txt}\n\n"
        f"*Condition d'entrée :*\n{condition}\n\n"
        f"*Si confirmé :*\n"
        f"▸ SL : {sl:,} $ | TP1 : {tp1:,} $ | TP2 : {tp2:,} $\n\n"
        f"_Pas un conseil financier — surveille et décide toi-même._"
    )


def signal_message(level, spot, score, checks, rsi, h4_bias, dxy, ema50):
    direction = "SHORT ⬇️" if level["direction"] == "below" else "LONG ⬆️"
    confluence_txt = "\n".join(f"{icon} {desc}" for icon, desc in checks)

    if level["direction"] == "below":
        sl = 4015
        tp1 = 3900
        tp2 = 3847
        entry = level["price"] - 9
        rr = round((entry - tp1) / (sl - entry), 1)
    else:
        sl = 3970
        tp1 = 4250
        tp2 = 4405
        entry = level["price"] + 9
        rr = round((tp1 - entry) / (entry - sl), 1)

    return (
        f"🚨 *SIGNAL — PRÊT À ENTRER* 🚨\n\n"
        f"Or/XAUUSD — *{direction}*\n"
        f"Clôture H1 confirmée au niveau {level['price']:,} $\n\n"
        f"*Confluence ({score}/4) :*\n{confluence_txt}\n\n"
        f"*Gestion du trade :*\n"
        f"▸ Zone d'entrée : ~{entry:,.0f} $\n"
        f"▸ Stop Loss : {sl:,} $\n"
        f"▸ TP1 : {tp1:,} $ — sortie partielle recommandée\n"
        f"▸ TP2 : {tp2:,} $ — sortie totale\n"
        f"▸ R:R estimé : *{rr}*\n\n"
        f"_Chacun gère son propre capital et son risque.\n"
        f"Pas un conseil financier._"
    )


def signal_filtered_message(level, spot, score, checks):
    direction = "SHORT" if level["direction"] == "below" else "LONG"
    confluence_txt = "\n".join(f"{icon} {desc}" for icon, desc in checks)
    return (
        f"⚠️ *Niveau cassé — confluence incomplète*\n\n"
        f"Niveau {direction} ({level['price']:,} $) franchi\n"
        f"Score : {score}/4 — attendre meilleure confluence\n\n"
        f"*Filtres :*\n{confluence_txt}\n\n"
        f"Or : {spot:,.0f} $ — pas de signal pour l'instant."
    )


def run():
    state = load_state()
    state.setdefault("fired", [])
    state.setdefault("last_h1_close", None)
    state.setdefault("last_h1_ts", 0)
    state.setdefault("last_analysis_ts", 0)
    state.setdefault("last_daily_ts", 0)
    state.setdefault("approach_alerted", [])
    state.setdefault("watch_alerted", [])

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

    for level in LEVELS[:2]:  # Seulement les 2 niveaux principaux (3959 et 4115)
        key = str(level["price"]) + level["direction"]
        if key in state["fired"]:
            continue

        score, checks = confluence_score(rsi, ema50, spot, h4_bias, level)

        # --- Stade 1 : En approche (50$) ---
        if is_approaching(spot, level, APPROACH_DISTANCE) and key not in state["approach_alerted"]:
            send_telegram_message(approach_message(level, spot, score, checks, "approach"))
            state["approach_alerted"].append(key)
            print(f"Alerte approche envoyée : {level['price']}")

        # --- Stade 2 : Surveillance active (20$) ---
        if is_approaching(spot, level, WATCH_DISTANCE) and key not in state["watch_alerted"]:
            send_telegram_message(approach_message(level, spot, score, checks, "watch"))
            state["watch_alerted"].append(key)
            print(f"Alerte surveillance envoyée : {level['price']}")

        # Reset des alertes si le prix s'éloigne du niveau
        if not is_approaching(spot, level, APPROACH_DISTANCE + 20):
            if key in state["approach_alerted"]:
                state["approach_alerted"].remove(key)
            if key in state["watch_alerted"]:
                state["watch_alerted"].remove(key)

    # --- Stade 3 : Clôture H1 confirmée ---
    if current_h1_ts != state["last_h1_ts"] and current_h1_close is not None:
        prev_h1_close = state["last_h1_close"]

        for level in LEVELS:
            key = str(level["price"]) + level["direction"]
            if key in state["fired"]:
                continue
            if not h1_crossed(prev_h1_close, current_h1_close, level):
                continue

            score, checks = confluence_score(rsi, ema50, spot, h4_bias, level)

            if score >= 3:
                send_telegram_message(signal_message(level, spot, score, checks, rsi, h4_bias, dxy, ema50))
                state["fired"].append(key)
                print(f"✅ Signal envoyé : {level['price']} (score {score}/4)")
            else:
                send_telegram_message(signal_filtered_message(level, spot, score, checks))
                print(f"Signal filtré {level['price']} (score {score}/4)")

        state["last_h1_close"] = current_h1_close
        state["last_h1_ts"] = current_h1_ts

    save_state(state)
    print("Vérification terminée.")


if __name__ == "__main__":
    run()
