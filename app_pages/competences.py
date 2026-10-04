"""Page « Compétences » : tes 6 notes, leur évolution et ta précision au clic."""
from datetime import datetime, timedelta, timezone

import altair as alt
import pandas as pd
import streamlit as st

from osuprog import db, model, replay, visuals
from osuprog.rating import load as load_ratings
from osuprog.skills import CATEGORIES, LABELS
from osuprog.ui import styled_chart

conn = db.connect()
ratings = load_ratings(conn)
values = {c: ratings[c]["rating"] for c in CATEGORIES}
games = {c: ratings[c]["games"] for c in CATEGORIES}
gaps = model.skill_gaps(values, games)
plan_shares = (db.get_meta(conn, "plan_summary") or {}).get("shares") or {}
priority = max(plan_shares, key=plan_shares.get) if plan_shares else None

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
    return None if before.empty else values[cat] - before["note"].iloc[-1]


def tag(cat: str) -> tuple[str | None, bool]:
    """Une seule étiquette par compétence, seulement quand elle dit quelque chose de sûr ou d'utile."""
    if games[cat] < model.CALIBRATED_GAMES:
        return "à mesurer", False
    if cat == priority:
        return "priorité", True
    if gaps[cat]["significant"]:
        return ("point fort", False) if gaps[cat]["gap"] > 0 else ("point faible", True)
    return None, False


st.title("Compétences")
st.caption("Ta note = la difficulté où tu atteins l'objectif une fois sur deux. À droite : l'évolution sur 7 jours.")

left, right = st.columns([1, 1], gap="large", vertical_alignment="center")
with left:
    st.html(visuals.radar(CATEGORIES, LABELS, values))
with right:
    rows = []
    for c in sorted(CATEGORIES, key=values.get, reverse=True):
        label, accent = tag(c)
        rows.append({"label": LABELS[c], "rating": values[c], "change": weekly_change(c), "tag": label,
                     "accent": accent})
    st.html(visuals.skill_list(rows))

st.subheader("Évolution")
if history.empty:
    st.caption("Pas encore assez de parties.")
else:
    order = sorted(CATEGORIES, key=lambda c: c != priority)
    chosen = st.segmented_control("Compétence", [LABELS[c] for c in order], default=LABELS[order[0]],
                                  label_visibility="collapsed", key="skill_chart") or LABELS[order[0]]
    cat = next(c for c in CATEGORIES if LABELS[c] == chosen)
    recent = history[(history["category"] == cat)
                     & (history["date"] >= history["date"].max() - pd.Timedelta(days=90))]
    daily = (recent.assign(jour=recent["date"].dt.tz_convert(None).dt.normalize())
             .groupby("jour", as_index=False)["note"].last())
    chart = alt.Chart(daily).mark_line(interpolate="monotone", strokeWidth=2.5, color=visuals.ACCENT,
                                       point=alt.OverlayMarkDef(size=28, color=visuals.ACCENT)).encode(
        x=alt.X("jour:T", title=None, axis=alt.Axis(format="%d/%m")),
        y=alt.Y("note:Q", title=None, scale=alt.Scale(zero=False)),
        tooltip=[alt.Tooltip("jour:T", title="Jour", format="%d/%m"),
                 alt.Tooltip("note:Q", title="Note", format=".2f")],
    ).properties(height=240)
    st.altair_chart(styled_chart(chart), width="stretch")

clicks = replay.summary(conn)
if clicks:
    st.subheader("Précision au clic")
    total = clicks["miss_aim"] + clicks["miss_timing"]
    b = clicks["bias"]
    with st.container(horizontal=True):
        st.metric("Régularité (UR)", f"{clicks['ur']:.0f}",
                  delta=None if clicks["ur_before"] is None else f"{clicks['ur'] - clicks['ur_before']:+.0f}",
                  delta_color="inverse", border=True,
                  help="Unstable rate de tes 30 dernières parties : plus c'est bas, plus tes clics sont réguliers. "
                       "La flèche compare aux 30 d'avant.")
        st.metric("Tendance", ("En avance" if b < 0 else "En retard") if clicks["bias_clear"] else "Centrée",
                  border=True,
                  help=f"En moyenne, tu tapes {abs(b):.0f} ms {'avant' if b < 0 else 'après'} le bon moment.")
        if total:
            st.metric("Miss de visée", f"{clicks['miss_aim'] / total:.0%}", border=True,
                      help="Part de tes miss où tu as cliqué au bon moment mais à côté. Le reste : trop tôt ou trop tard.")
    st.caption("D'après tes replays de lazer. Détails dans le Guide.")
