"""Page « Aujourd'hui » : la séance du jour, dans l'ordre. Une map à jouer maintenant, puis la suite."""
from datetime import date

import streamlit as st

from osuprog import db, planner, rewards, visuals
from osuprog.api import MissingCredentials, OsuApi
from osuprog.rating import load as load_ratings
from osuprog.ui import item_state, map_card

conn = db.connect()
ratings = load_ratings(conn)
day = date.today().isoformat()
INTENSITY_LABELS = {"confort": "Confort", "équilibré": "Équilibré", "intense": "Intense"}


def api_or_none():
    try:
        return OsuApi()
    except MissingCredentials:
        return None


# Nouveau jour ou nouveaux réglages sans synchro : on prépare quand même la séance depuis le catalogue local.
planner.build_session(conn, None, ratings, day, log=lambda *_: None)
plan = conn.execute("SELECT * FROM session_plan WHERE day = ? ORDER BY pos", (day,)).fetchall()
cfg = planner.settings(conn)

items = []
for row in plan:
    state, _ = item_state(conn, row["role"], row["category"], row["beatmap_id"], day)
    items.append({**dict(row), "state": state})
played = sum(i["state"] != "todo" for i in items)
goals = [i for i in items if i["role"] in ("Objectif", "Examen")]
goals_met = sum(i["state"] == "done" for i in goals)
total_minutes = sum(i["minutes"] or 0 for i in items)
streak = rewards.streaks(rewards.play_days(conn), day)

# --- En-tête : titre, réglages, résumé ----------------------------------------------------------
with st.container(horizontal=True, vertical_alignment="bottom"):
    st.title("Séance du jour")
    st.space("stretch")
    with st.popover("Réglages", icon=":material/tune:", type="tertiary"):
        duration_options = ["Auto", "30 min", "45 min", "60 min", "90 min"]
        current_duration = "Auto" if cfg["auto_minutes"] else f"{cfg['minutes']} min"
        chosen_duration = st.segmented_control(
            "Durée", duration_options, default=current_duration if current_duration in duration_options else "Auto",
            help=f"« Auto » reprend la durée de tes séances habituelles (≈ {planner.typical_session_minutes(conn)} min).")
        chosen_intensity = st.segmented_control(
            "Difficulté", list(INTENSITY_LABELS.values()), default=INTENSITY_LABELS[cfg["intensity"]],
            help="Confort : maps que tu finis presque toujours. Intense : plus dures, plus d'échecs.")
        new_minutes = None if chosen_duration in (None, "Auto") else int(chosen_duration.split()[0])
        new_intensity = {v: k for k, v in INTENSITY_LABELS.items()}.get(chosen_intensity, cfg["intensity"])
        if (new_minutes != db.get_meta(conn, "session_minutes")) or new_intensity != cfg["intensity"]:
            if new_minutes is None:
                db.del_meta(conn, "session_minutes")
            else:
                db.set_meta(conn, "session_minutes", new_minutes)
            db.set_meta(conn, "intensity", new_intensity)
            conn.commit()
            st.rerun()
        if st.button("Proposer d'autres maps", icon=":material/shuffle:", width="stretch",
                     help="Même logique, autres maps. Celles déjà jouées restent."):
            planner.reset_session(conn, day)
            with st.spinner("Choix des maps…"):
                planner.build_session(conn, api_or_none(), ratings, day, log=lambda *_: None)
            st.rerun()

facts = [(f"{played}/{len(items)}", "maps"), (f"≈ {total_minutes:.0f}", "min")]
if goals:
    facts.append((f"{goals_met}/{len(goals)}", "objectifs"))
if streak["current"]:
    facts.append((str(streak["current"]), "jours de suite"))
st.html(visuals.summary(facts))
st.html(visuals.progress(played / len(items) if items else 0))


def card(i, *, highlight=False):
    map_card(conn, i["beatmap_id"], role=i["role"], cat=i["category"], level_c=i["level_c"],
             number=items.index(i) + 1, day=day, highlight=highlight, reason=i["reason"], p=i["p"],
             minutes=i["minutes"], ignorable=i["role"] != "Examen" and i["state"] in ("todo", "retry"), key="today")


todo = [i for i in items if i["state"] == "todo"]
retry = [i for i in items if i["state"] == "retry"]
finished = [i for i in items if i["state"] == "done"]
current = (todo or retry or [None])[0]

st.space("small")
if not items:
    st.info("Pas encore de maps pour aujourd'hui : clique sur **Synchroniser**.", icon=":material/sync:")
elif current is None:
    st.success("Séance terminée. Bravo !", icon=":material/check_circle:")
else:
    card(current, highlight=True)

rest = [i for i in todo + retry if i is not current]
if rest:
    st.subheader("Ensuite")
    for i in rest:
        card(i)

if finished:
    with st.expander(f"Terminées ({len(finished)})"):
        for i in finished:
            card(i)

if items:
    st.space("small")
    if st.button("Rajouter un entraînement (15 min)", icon=":material/more_time:", width="stretch",
                 help="Ajoute des maps à la fin de la séance, quand tu veux, même si elle n'est pas finie."):
        planner.add_session_time(conn, day, 15)
        with st.spinner("Choix des maps…"):
            planner.build_session(conn, api_or_none(), ratings, day, log=lambda *_: None)
        st.rerun()
