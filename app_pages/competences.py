"""Page « Mes compétences » : chaque note expliquée en clair, et son évolution."""
from datetime import datetime, timedelta, timezone

import altair as alt
import pandas as pd
import streamlit as st

from osuprog import db, model, replay, visuals
from osuprog.rating import load as load_ratings
from osuprog.skills import CATEGORIES, CRITERIA, LABELS
from osuprog.ui import SKILL_ICONS, SKILL_PLAIN, criterion_text, stars, styled_chart

conn = db.connect()
ratings = load_ratings(conn)
values = {c: ratings[c]["rating"] for c in CATEGORIES}
games = {c: ratings[c]["games"] for c in CATEGORIES}
strongest = model.strongest_skill(values, games)
gaps = model.skill_gaps(values, games)
plan_shares = (db.get_meta(conn, "plan_summary") or {}).get("shares") or {}
priority = [s for s in sorted(plan_shares, key=plan_shares.get, reverse=True)][:2]

history = pd.DataFrame(
    conn.execute(
        """SELECT e.category, e.rating_after AS note, s.ended_at FROM rating_events e
           JOIN scores s ON s.score_id = e.score_id
           WHERE s.source != 'inferred_fail' ORDER BY s.ended_at"""
    ).fetchall(),
    columns=["category", "note", "ended_at"],
)
history["date"] = pd.to_datetime(history["ended_at"], utc=True)
week_ago = datetime.now(timezone.utc) - timedelta(days=7)


def weekly_change(cat: str) -> float | None:
    before = history[(history["category"] == cat) & (history["date"] < week_ago)]
    if before.empty:
        return None
    return ratings[cat]["rating"] - before["note"].iloc[-1]


st.html(visuals.page_header("insights", "Mes compétences",
                            "Jusqu'à quelle difficulté tu joues chaque type de map proprement", "#ff66aa"))
clear = db.get_meta(conn, "clear_level")
acc_example = CRITERIA["aim"].min_acc


def badge_for(cat: str) -> str | None:
    if ratings[cat]["games"] < model.CALIBRATED_GAMES:
        return "à mesurer"
    if priority and cat == priority[0]:
        return "priorité 1"
    if len(priority) > 1 and cat == priority[1]:
        return "priorité 2"
    if cat == strongest and gaps[cat]["significant"]:
        return "point fort"
    return None


left, right = st.columns([5, 6], gap="large", vertical_alignment="center")
with left:
    st.html(visuals.radar(CATEGORIES, LABELS, values, {c: gaps[c]["skill_sd"] for c in CATEGORIES},
                          clear["level"] if clear else None))
with right:
    st.html(visuals.skill_grid([
        visuals.skill_tile(c, LABELS[c], values[c], gaps[c]["skill_sd"], weekly_change(c), badge_for(c),
                           gaps[c]["significant"], gaps[c]["gap"])
        for c in CATEGORIES
    ]))
    clear_low = [c for c in CATEGORIES if gaps[c]["significant"] and gaps[c]["gap"] < 0]
    clear_high = [c for c in CATEGORIES if gaps[c]["significant"] and gaps[c]["gap"] > 0]
    signal = []
    if clear_low:
        signal.append("nettement **en dessous** : " + ", ".join(f"**{LABELS[c]}**" for c in clear_low))
    if clear_high:
        signal.append("nettement **au-dessus** : " + ", ".join(f"**{LABELS[c]}**" for c in clear_high))
    if signal:
        st.caption(":material/troubleshoot: **Ce qui ressort vraiment** (écart plus grand que la marge d'erreur) : "
                   + " ; ".join(signal) + ". Les autres écarts sont dans la marge d'erreur : le programme les "
                   "travaille quand même, mais rien ne prouve encore qu'ils sont réels.")
    else:
        st.caption(":material/troubleshoot: **Tes compétences sont proches les unes des autres** : aucun écart ne "
                   "dépasse la marge d'erreur. Les priorités du programme sont des paris raisonnables, pas des "
                   "certitudes.")

if clear:
    st.caption(
        f":material/lightbulb: **Tu finis des maps jusqu'à {stars(clear['level'])}** (une sur deux, sans fail ; ta plus "
        f"haute map réussie est à {stars(clear['highest'])}), en jaune sur l'hexagone. Tes notes sont plus basses, "
        f"et c'est normal : il faut aussi la jouer proprement (par exemple au moins {acc_example:g} % de précision en "
        "Aim). L'objectif du jeu : rapprocher les deux.")

with st.expander("Ce que veut dire chaque note", icon=":material/menu_book:"):
    for cat in CATEGORIES:
        r = ratings[cat]
        extra = (" Encore trop peu de parties : les **tests de niveau** du programme vont l'affiner."
                 if r["games"] < model.CALIBRATED_GAMES else f" Calculée à partir de {r['games']:.0f} parties (pondérées).")
        st.markdown(f"{SKILL_ICONS[cat]} **{LABELS[cat]}**, c'est {SKILL_PLAIN[cat]}. Sur une map {LABELS[cat]} de "
                    f"{stars(r['rating'])}, tu fais {criterion_text(CRITERIA[cat])} environ une fois sur deux.{extra}")

st.subheader("Évolution de tes notes", icon=":material/trending_up:")
recent = history[history["date"] >= history["date"].max() - pd.Timedelta(days=90)] if not history.empty else history
if recent.empty:
    st.caption("Pas encore assez de parties datées pour tracer une courbe.")
else:
    daily = (recent.assign(jour=recent["date"].dt.tz_convert(None).dt.normalize(),
                           compétence=recent["category"].map(LABELS))
             .groupby(["jour", "compétence"], as_index=False)["note"].last())
    chart = alt.Chart(daily).mark_line(point=alt.OverlayMarkDef(size=40, filled=True), interpolate="monotone",
                                       strokeWidth=3).encode(
        x=alt.X("jour:T", title=None),
        y=alt.Y("note:Q", title="Note (★)", scale=alt.Scale(zero=False)),
        color=alt.Color("compétence:N", title=None, legend=alt.Legend(orient="bottom"),
                        scale=alt.Scale(domain=[LABELS[c] for c in CATEGORIES],
                                        range=[visuals.SKILL_COLORS[c] for c in CATEGORIES])),
        tooltip=[alt.Tooltip("jour:T", title="Jour"), "compétence:N", alt.Tooltip("note:Q", format=".2f")],
    ).properties(height=320)
    st.altair_chart(styled_chart(chart), width="stretch")
    st.caption("Les 90 derniers jours où tu as joué. Chaque point est ta note à la fin de la journée.")

st.subheader("Ta précision au clic", icon=":material/ads_click:")
clicks = replay.summary(conn)
if clicks is None:
    st.caption("Pas encore assez de replays analysés : ils viennent de lazer sur ce PC (Node.js nécessaire).")
else:
    b = clicks["bias"]
    total = clicks["miss_aim"] + clicks["miss_timing"]
    trend = ("plus bas = plus régulier" if clicks["ur_before"] is None else
             f"unstable rate · {clicks['ur'] - clicks['ur_before']:+.0f} vs avant")
    st.html(visuals.stat_tiles([
        ("graphic_eq", f"{clicks['ur']:.0f}", trend,
         "#5cf08f" if clicks["ur_before"] and clicks["ur"] < clicks["ur_before"] else "#66ccff"),
        ("swap_horiz", (("en avance" if b < 0 else "en retard") if clicks["bias_clear"] else "centrée")
         + f" · {abs(b):.0f} ms", "tendance au clic", "#ffd23f"),
        ("my_location", f"{clicks['miss_aim'] / total:.0%}" if total else "–", "miss de visée", "#ff66aa"),
        ("music_note", f"{clicks['miss_timing'] / total:.0%}" if total else "–", "miss de rythme", "#aa88ff"),
    ]))
    st.caption(f"Médiane de tes {clicks['n']} dernières parties analysées, comparée aux {clicks['n']} d'avant. "
               "L'unstable rate dépend aussi des maps jouées : compare-le sur plusieurs semaines.")
    tips = []
    if clicks["bias_clear"]:
        tips.append(f"Tu tapes régulièrement **{abs(clicks['bias']):.0f} ms {'trop tôt' if clicks['bias'] < 0 else 'trop tard'}**. "
                    "Si c'est pareil sur toutes les maps, c'est sans doute ton décalage audio : lazer peut le "
                    "calibrer d'après tes parties (réglages audio, décalage).")
    if total >= 10:
        share = clicks["miss_aim"] / total
        if share >= 0.6:
            tips.append("La plupart de tes miss sont des **clics à côté de la note** : c'est la visée qui coûte le plus "
                        "(sauts, précision du curseur).")
        elif share <= 0.4:
            tips.append("La plupart de tes miss sont des **clics au mauvais moment** : rythme ou lecture des notes, "
                        "plus que la visée.")
        else:
            tips.append("Tes miss se partagent entre visée et rythme : pas de cause unique.")
    for tip in tips:
        st.markdown(f"- {tip}")
    hist = pd.DataFrame(clicks["history"], columns=["ended_at", "ur", "biais"])
    hist["jour"] = pd.to_datetime(hist["ended_at"], utc=True).dt.tz_convert(None).dt.normalize()
    daily_ur = hist.groupby("jour", as_index=False)["ur"].median()
    if len(daily_ur) >= 3:
        base = alt.Chart(daily_ur).encode(
            x=alt.X("jour:T", title=None),
            y=alt.Y("ur:Q", title="Unstable rate (médiane du jour)",
                    scale=alt.Scale(domain=[max(0, daily_ur["ur"].min() - 40), daily_ur["ur"].max() + 40])),
            tooltip=[alt.Tooltip("jour:T", title="Jour"), alt.Tooltip("ur:Q", title="UR", format=".0f")],
        )
        area = base.mark_area(interpolate="monotone", clip=True, line={"color": "#66ccff", "strokeWidth": 3}, color=alt.Gradient(
            gradient="linear", x1=1, x2=1, y1=1, y2=0,
            stops=[alt.GradientStop(color="rgba(102,204,255,0)", offset=0),
                   alt.GradientStop(color="rgba(102,204,255,0.45)", offset=1)]))
        st.altair_chart(styled_chart((area + base.mark_point(filled=True, size=50, color="#66ccff"))
                                     .properties(height=220)), width="stretch")
    st.caption("Calculé en rejouant tes replays de lazer contre les notes de chaque map. Reconstitution simplifiée : "
               "fiable en tendance, pas à la note près.")

with st.expander("Comment la note est calculée ?", icon=":material/calculate:"):
    st.markdown(
        "Chaque partie est un petit match entre toi et la map, comme un classement Elo aux échecs :\n\n"
        "1. L'outil regarde à quelle(s) compétence(s) la map correspond (une map à streams compte surtout "
        "pour Speed, une map à OD élevé pour Accuracy…).\n"
        "2. Il prédit tes chances d'atteindre l'objectif, d'après ta note et les étoiles de la map.\n"
        "3. Si tu fais mieux que prévu, ta note monte ; moins bien, elle baisse. Réussir une map plus dure "
        "que ta note rapporte beaucoup, rater une map facile coûte cher.\n\n"
        "Au début, les notes bougent vite ; plus tu joues, plus elles se stabilisent. Tes anciennes parties "
        "(avant le premier lancement de l'outil) comptent moitié."
    )
