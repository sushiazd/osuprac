"""Page « Historique » : toutes les parties analysées."""
import json

import pandas as pd
import streamlit as st

from osuprog import db
from osuprog.mods import mods_label
from osuprog.skills import LABELS

SOURCES = {
    "best": "Top play",
    "recent": "Récente",
    "history": "En ligne",
    "lazer_local": "Lazer (ce PC)",
    "inferred_fail": "Jamais passée",
}

conn = db.connect()
rows = conn.execute(
    """SELECT s.ended_at, b.artist, b.title, b.version, s.mods, s.accuracy, s.n_miss, s.passed, s.source,
              a.stars, a.primary_cat,
              (SELECT group_concat(e.category || ' ' || printf('%+.2f', e.delta), ', ')
               FROM rating_events e WHERE e.score_id = s.score_id AND abs(e.delta) >= 0.005) AS effect
       FROM scores s
       LEFT JOIN beatmaps b ON b.beatmap_id = s.beatmap_id
       LEFT JOIN map_attrs a ON a.beatmap_id = s.beatmap_id AND a.mods_key = s.mods_key
       WHERE s.countable = 1
       ORDER BY s.ended_at DESC"""
).fetchall()

st.title("Historique", icon=":material/history:")
st.markdown(f"Les **{len(rows)} parties** que l'outil a récupérées, en ligne et dans lazer sur ce PC. "
            "La colonne *Effet* montre de combien chaque partie a fait bouger tes notes.")

view = st.segmented_control("Afficher", ["Toutes", "Réussies", "Fails"], default="Toutes",
                            label_visibility="collapsed")
df = pd.DataFrame([
    {
        "Date": None if r["source"] == "inferred_fail" else pd.to_datetime(r["ended_at"], utc=True).tz_convert(None),
        "Map": f"{r['artist']} - {r['title']} [{r['version']}]",
        "Mods": mods_label(json.loads(r["mods"])),
        "Étoiles": r["stars"],
        "Précision": None if r["source"] == "inferred_fail" else r["accuracy"],
        "Miss": None if r["source"] == "inferred_fail" else r["n_miss"],
        "Réussie": bool(r["passed"]),
        "Type": LABELS.get(r["primary_cat"], "-"),
        "Effet": (r["effect"] or "").replace(".", ","),
        "Source": SOURCES.get(r["source"], r["source"]),
    }
    for r in rows
])
if view == "Réussies":
    df = df[df["Réussie"]]
elif view == "Fails":
    df = df[~df["Réussie"]]

st.dataframe(
    df, hide_index=True, width="stretch", height=560,
    column_config={
        "Date": st.column_config.DatetimeColumn(format="DD/MM/YY HH:mm"),
        "Map": st.column_config.TextColumn(width="large"),
        "Étoiles": st.column_config.NumberColumn(format="%.2f ★"),
        "Précision": st.column_config.ProgressColumn(format="%.2f %%", min_value=0, max_value=100),
        "Réussie": st.column_config.CheckboxColumn(),
        "Effet": st.column_config.TextColumn(help="Variation de tes notes due à cette partie"),
    },
)
st.caption("« Jamais passée » : une map classée jouée au moins 3 fois sans aucun score enregistré par osu!.")
