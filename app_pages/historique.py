"""Page « Historique » : toutes les parties analysées."""
import json
import statistics

import pandas as pd
import streamlit as st

from osuprog import db
from osuprog.mods import mods_label
from osuprog.skills import LABELS

GRADES = {"XH": "SS", "X": "SS", "SH": "S", "S": "S", "A": "A", "B": "B", "C": "C", "D": "D", "F": "F"}



def effect_text(effect: str | None) -> str:
    """« accuracy +0.02, aim -0.01 » -> « Accuracy +0,02 · Aim -0,01 »."""
    if not effect:
        return ""
    parts = []
    for item in effect.split(", "):
        cat, _, delta = item.partition(" ")
        parts.append(f"{LABELS.get(cat, cat)} {delta.replace('.', ',')}")
    return " · ".join(parts)


conn = db.connect()
rows = conn.execute(
    """SELECT s.ended_at, b.title, b.version, s.mods, s.accuracy, s.n_miss, s.passed, s.source, s.grade,
              a.stars, r.ur,
              (SELECT group_concat(e.category || ' ' || printf('%+.2f', e.delta), ', ')
               FROM rating_events e WHERE e.score_id = s.score_id AND abs(e.delta) >= 0.005) AS effect
       FROM scores s
       LEFT JOIN beatmaps b ON b.beatmap_id = s.beatmap_id
       LEFT JOIN map_attrs a ON a.beatmap_id = s.beatmap_id AND a.mods_key = s.mods_key
       LEFT JOIN replay_stats r ON r.score_id = s.score_id
       WHERE s.countable = 1 AND s.source != 'inferred_fail'
       ORDER BY s.ended_at DESC"""
).fetchall()
passed = [r for r in rows if r["passed"]]

st.title("Historique")
with st.container(horizontal=True):
    st.metric("Parties", len(rows), border=True)
    st.metric("Finies", f"{len(passed) / len(rows):.0%}" if rows else "–", border=True)
    st.metric("Précision médiane", f"{statistics.median(r['accuracy'] for r in passed):.1f} %".replace(".", ",")
              if passed else "–", border=True)

view = st.segmented_control("Afficher", ["Toutes", "Finies", "Fails"], default="Toutes",
                            label_visibility="collapsed")
df = pd.DataFrame([
    {
        "Date": pd.to_datetime(r["ended_at"], utc=True).tz_convert(None),
        "Map": f"{r['title']} [{r['version']}]",
        "★": r["stars"],
        "Précision": r["accuracy"],
        "Miss": r["n_miss"],
        "Rang": GRADES.get(r["grade"], r["grade"] or "–") if r["passed"] else "F",
        "Mods": mods_label(json.loads(r["mods"])),
        "UR": f"{r['ur']:.0f}" if r["ur"] else "",
        "Effet": effect_text(r["effect"]),
        "passed": bool(r["passed"]),
    }
    for r in rows
])
for col in ("★", "Précision", "Miss"):  # vides plutôt que « None » quand la valeur manque
    df[col] = pd.to_numeric(df[col], errors="coerce")
if view == "Finies":
    df = df[df["passed"]]
elif view == "Fails":
    df = df[~df["passed"]]

st.dataframe(
    df.drop(columns="passed"), hide_index=True, width="stretch", height=600,
    column_config={
        "Date": st.column_config.DatetimeColumn(format="DD/MM HH:mm"),
        "Map": st.column_config.TextColumn(width="large"),
        "★": st.column_config.NumberColumn(format="%.2f"),
        "Précision": st.column_config.NumberColumn(format="%.2f %%"),
        "UR": st.column_config.TextColumn(help="Unstable rate (d'après le replay de lazer)"),
        "Effet": st.column_config.TextColumn(help="Comment cette partie a fait bouger tes notes"),
    },
)
