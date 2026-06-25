"""
Analyse horaire complète — publiée toutes les heures sur le canal Telegram.
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


def build_analysis_prompt(spot, rsi, ema50, h4_bias, fib, dxy, h1_candles, h4_candles, daily_candles):
    h1_text = "\n".join(
        f"  {datetime.utcfromtimestamp(c['time']).strftime('%H:%M')} — "
        f"O={c['open']:.0f} H={c['high']:.0f} L={c['low']:.0f} C={c['close']:.0f}"
        for c in h1_candles[-6:]
    )
    h4_text = "\n".join(
        f"  O={c['open']:.0f} H={c['high']:.0f} L={c['low']:.0f} C={c['close']:.0f}"
        for c in h4_candles[-4:]
    )
    daily_text = "\n".join(
        f"  {c['date']} — O={c['open']:.0f} H={c['high']:.0f} L={c['low']:.0f} C={c['close']:.0f}"
        for c in daily_candles[-3:]
    )

    fib_block = ""
    if fib:
        fib_block = f"""
Niveaux Fibonacci (swing {fib['high']:.0f} → {fib['low']:.0f}) :
  0.382 = {fib['fib_382']:.0f} $ | 0.500 = {fib['fib_500']:.0f} $ | 0.618 = {fib['fib_618']:.0f} $ | 0.786 = {fib['fib_786']:.0f} $
"""

    return f"""Tu es un analyste senior spécialisé dans le trading de l'Or (XAUUSD), expert en SMC (Smart Money Concepts), VSA (Volume Spread Analysis) et analyse intermarché.

Tu gères un canal Telegram de trading dont la communauté suit activement le marché de l'Or. Chaque heure, tu publies une analyse complète pour aider les membres à comprendre la situation et les scénarios possibles.

--- DONNÉES DU MARCHÉ ---

Prix actuel : {spot:,.0f} $
Heure UTC : {datetime.utcnow().strftime('%H:%M')}

Indicateurs H1 :
  RSI(14) = {rsi if rsi else 'N/D'}
  EMA50 = {f'{ema50:.0f}' if ema50 else 'N/D'} $ (prix {'AU-DESSUS' if ema50 and spot > ema50 else 'EN-DESSOUS'})
  Biais H4 (EMA20 H4) = {h4_bias if h4_bias else 'N/D'}
  DXY = {f'{dxy:.2f}' if dxy else 'N/D'} (corrélation inverse avec l'Or)
{fib_block}
Niveaux SMC clés :
  PDH (haut d'hier) = 4 115 $ → confirmation LONG si clôture H1 au-dessus
  PDL (bas d'hier)  = 3 959 $ → confirmation SHORT si clôture H1 en-dessous
  SL LONG = 3 970 $ | Objectifs : 4 250 $ puis 4 405 $
  SL SHORT = 4 015 $ | Objectifs : 3 900 $ puis 3 847 $

Dernières bougies H1 (6 dernières) :
{h1_text}

Dernières bougies H4 (4 dernières) :
{h4_text}

Dernières bougies Daily (3 dernières) :
{daily_text}

--- INSTRUCTIONS ---

Rédige une analyse complète pour le canal Telegram, en français, avec ce format EXACT :

🪙 *ANALYSE Or/XAUUSD — [HEURE]h UTC*

📍 *Structure de marché*
[Décris la structure actuelle : BOS, CHoCH, tendance sur H1 et H4. Où en est-on par rapport aux niveaux clés ?]

📊 *Indicateurs techniques*
[RSI : que signifie le niveau actuel ? Surachat/survente/neutre ?]
[EMA50 : le prix est au-dessus ou en-dessous ? Qu'est-ce que ça implique ?]
[DXY : impact sur l'Or en ce moment ?]

🎯 *Scénarios actifs*
🔵 LONG : [Condition de confirmation + SL + objectifs + probabilité subjective]
🟠 SHORT : [Condition de confirmation + SL + objectifs + probabilité subjective]

⚡ *Confluence du moment*
[Quels indicateurs s'alignent ? Y a-t-il un niveau Fibonacci proche ? Le biais H4 confirme-t-il un scénario ?]

👁 *Ce qu'on surveille cette heure*
[1-2 choses concrètes à observer sur le graphique pour la prochaine heure]

_Analyse automatisée — pas un conseil financier._

Règles importantes :
- Max 280 mots au total
- Sois direct et précis, pas de formules vagues
- Si le marché est indécis, dis-le clairement
- Mentionne toujours les deux scénarios avec leurs niveaux
- Utilise les emojis du format ci-dessus
"""


def run():
    candles_h1 = get_hourly_candles(60)
    h4_candles = get_h4_candles(30)
    daily_candles = get_daily_candles(5)
    rsi = get_rsi(candles_h1)
    ema50 = get_ema(candles_h1, 50)
    h4_bias = get_h4_bias(h4_candles)
    fib = get_fibonacci_levels(candles_h1, 50)
    dxy = get_dxy_price()
    spot = get_gold_price()

    prompt = build_analysis_prompt(spot, rsi, ema50, h4_bias, fib, dxy, candles_h1, h4_candles, daily_candles)

    try:
        response = get_gemini_client().models.generate_content(model=ANALYSIS_MODEL, contents=prompt)
        message = response.text
        print("Analyse Gemini générée.")
    except Exception as e:
        # Fallback sans Gemini
        ema_pos = "au-dessus" if ema50 and spot > ema50 else "en-dessous"
        message = (
            f"📊 *Or/XAUUSD — {datetime.utcnow().strftime('%H:%M')} UTC*\n\n"
            f"Prix : {spot:,.0f} $\n"
            f"RSI(14) : {rsi} | EMA50 : {ema50:.0f} $ (prix {ema_pos})\n"
            f"Biais H4 : {h4_bias} | DXY : {dxy:.2f}\n\n"
            f"Niveaux à surveiller :\n"
            f"✅ LONG > 4 115 $ (SL 3 970, obj 4 250/4 405)\n"
            f"⛔ SHORT < 3 959 $ (SL 4 015, obj 3 900/3 847)\n\n"
            f"_Analyse IA indisponible — données brutes._"
        )
        print(f"Fallback envoyé (Gemini KO : {e})")

    send_telegram_message(message)
    print(f"Message envoyé. Prix : {spot:.0f}$")


if __name__ == "__main__":
    run()
