"""Page « Mes compétences » : chaque note expliquée en clair, et son évolution."""
from datetime import datetime, timedelta, timezone

import altair as alt
import pandas as pd
import streamlit as st

from osuprog import db, progression
from osuprog.rating import load as load_ratings
from osuprog.skills import CATEGORIES, CRITERIA, LABELS
from osuprog.ui import SKILL_ICONS, SKILL_PLAIN, criterion_text, stars

conn = db.connect()
ratings = load_ratings(conn)
weakest, second, strongest = progression.focus_skills(ratings)
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


st.title("Mes compétences", icon=":material/insights:")
st.markdown(
    "Chaque compétence a une **note en étoiles**. Elle répond à une question simple : "
    "*jusqu'à quelle difficulté tu joues ce type de map proprement ?* Plus précisément, c'est le niveau "
    "d'étoiles où tu atteins l'objectif de la compétence **environ une fois sur deux**."
)
clear = db.get_meta(conn, "clear_level")
acc_example = CRITERIA["aim"].min_acc
if clear:
    st.info(
        f"**Tu réussis des maps jusqu'à {stars(clear['level'])}** (une sur deux, sans fail ; ta plus haute map "
        f"réussie est à {stars(clear['highest'])}). Tes notes ci-dessous sont **plus basses**, et c'est normal : "
        f"finir une map ne suffit pas, il faut aussi la jouer proprement (par exemple au moins {acc_example:g} % "
        "de précision en Aim). L'objectif du jeu est de rapprocher les deux.",
        icon=":material/lightbulb:",
    )
else:
    st.info(
        "Finir une map ne suffit pas pour monter une note : il faut aussi la jouer proprement "
        f"(par exemple au moins {acc_example:g} % de précision en Aim).",
        icon=":material/lightbulb:",
    )

for row in (CATEGORIES[:3], CATEGORIES[3:]):
    for col, cat in zip(st.columns(3), row):
        r = ratings[cat]
        with col.container(border=True, height="stretch"):
            with st.container(horizontal=True, vertical_alignment="center"):
                st.markdown(f"#### {SKILL_ICONS[cat]} {LABELS[cat]}")
                if r["games"] < progression.CALIBRATED_GAMES:
                    st.badge("À mesurer", icon=":material/straighten:", color="gray")
                elif priority and cat == priority[0]:
                    st.badge("Priorité n°1", icon=":material/flag:", color="red")
                elif len(priority) > 1 and cat == priority[1]:
                    st.badge("Priorité n°2", color="orange")
                elif cat == strongest:
                    st.badge("Point fort", icon=":material/emoji_events:", color="green")
            change = weekly_change(cat)
            trend = history.loc[history["category"] == cat, "note"].tail(30).round(2).tolist()
            spark = {"chart_data": trend, "chart_type": "area"} if len(trend) >= 3 else {}
            if change is not None and abs(change) >= 0.01:
                st.metric("Note", stars(r["rating"]), label_visibility="collapsed", **spark,
                          delta=f"{change:+.2f}★ en 7 jours".replace(".", ","))
            else:
                st.metric("Note", stars(r["rating"]), label_visibility="collapsed", **spark,
                          delta="stable sur 7 jours", delta_color="off", delta_arrow="off")
            st.caption(f"**C'est quoi ?** {SKILL_PLAIN[cat].capitalize()}.")
            st.caption(f"**Ta note veut dire :** sur une map {LABELS[cat]} de {stars(r['rating'])}, tu fais "
                       f"{criterion_text(CRITERIA[cat])} environ une fois sur deux.")
            if r["games"] < progression.CALIBRATED_GAMES:
                st.caption(":material/straighten: Encore trop peu de parties pour être sûr : "
                           "les **tests de niveau** du programme du jour vont affiner cette note.")
            else:
                st.caption(f"Calculée à partir de {r['games']:.0f} parties (pondérées).")

st.subheader("Évolution de tes notes", icon=":material/trending_up:")
recent = history[history["date"] >= history["date"].max() - pd.Timedelta(days=90)] if not history.empty else history
if recent.empty:
    st.caption("Pas encore assez de parties datées pour tracer une courbe.")
else:
    daily = (recent.assign(jour=recent["date"].dt.tz_convert(None).dt.normalize(),
                           compétence=recent["category"].map(LABELS))
             .groupby(["jour", "compétence"], as_index=False)["note"].last())
    chart = alt.Chart(daily).mark_line(point=True, interpolate="monotone").encode(
        x=alt.X("jour:T", title=None),
        y=alt.Y("note:Q", title="Note (★)", scale=alt.Scale(zero=False)),
        color=alt.Color("compétence:N", title=None, legend=alt.Legend(orient="bottom")),
        tooltip=[alt.Tooltip("jour:T", title="Jour"), "compétence:N", alt.Tooltip("note:Q", format=".2f")],
    ).properties(height=320)
    st.altair_chart(chart, width="stretch")
    st.caption("Les 90 derniers jours où tu as joué. Chaque point est ta note à la fin de la journée.")

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
