"""Page « Progression » : niveau validé, paliers par compétence et examen."""
import streamlit as st

from osuprog import db, fmt, progression, visuals
from osuprog.progression import STEP_C
from osuprog.rating import load as load_ratings
from osuprog.skills import CATEGORIES, CRITERIA, LABELS
from osuprog.ui import SKILL_ICONS, criterion_text, map_card, map_info, skill_label, stars

conn = db.connect()
ratings = load_ratings(conn)
exam = progression.exam_state(conn)
progress = progression.load_progress(conn)
rank, target = exam["rank_c"], exam["target_c"]
ready = [c for c in CATEGORIES if progress.get(c, 0) >= target]

st.html(visuals.page_header("stairs", "Ta progression",
                            "Valide les paliers de chaque compétence, puis passe l'examen du niveau suivant", "#8866ff"))
st.html(visuals.rank_emblem(rank, target, len(ready), len(CATEGORIES)))
st.caption(
    f"Un palier tous les {fmt.level(STEP_C)} : réussis ses **{progression.MAPS_PER_TIER} maps** (objectif de la "
    f"compétence) pour le valider. Ton niveau avance de {fmt.level(progression.RANK_STEP_C)} quand **toutes** tes "
    "compétences ont atteint le niveau suivant et que tu as réussi l'examen.")


def tier_maps(cat: str, level_c: int) -> list:
    return conn.execute("SELECT beatmap_id FROM tier_maps WHERE category = ? AND level_c = ? ORDER BY slot",
                        (cat, level_c)).fetchall()


def met_count(cat: str, maps: list) -> int:
    return sum(1 for m in maps if (r := progression.best_result(conn, m["beatmap_id"], CRITERIA[cat])) and r["met"])


st.subheader("Passe de paliers", icon=":material/military_tech:")
tracks = []
for cat in CATEGORIES:
    current = progress.get(cat, 0) + STEP_C
    maps = tier_maps(cat, current)
    tracks.append(visuals.tier_track(cat, LABELS[cat], current, STEP_C, met_count(cat, maps),
                                     max(len(maps), progression.MAPS_PER_TIER)))
st.html("".join(tracks))

if exam["unlocked"]:
    st.subheader(f"Examen du niveau {stars(target / 100)}", icon=":material/school:")
    st.success(f"Toutes tes compétences sont prêtes : réussis l'**examen** ({criterion_text(progression.EXAM_CRITERION)} "
               "sur chaque map) pour monter de niveau.", icon=":material/workspace_premium:")
    for m in tier_maps("exam", target):
        map_card(conn, m["beatmap_id"], cat="exam", level_c=target, key="exam")
else:
    missing = ", ".join(LABELS[c] for c in exam["missing"])
    st.caption(f":material/lock: L'examen du niveau {stars(target / 100)} se débloque quand ces compétences ont "
               f"validé leur palier {stars(target / 100)} : {missing}.")

st.subheader("Maps des paliers en cours", icon=":material/flag:")
tabs = st.tabs([f"{LABELS[c]} · {stars((progress.get(c, 0) + STEP_C) / 100)}" for c in CATEGORIES])
for tab, cat in zip(tabs, CATEGORIES):
    with tab:
        current = progress.get(cat, 0) + STEP_C
        maps = tier_maps(cat, current)
        st.caption(f"{SKILL_ICONS[cat]} **Palier {stars(current / 100)}** : {met_count(cat, maps)}/{len(maps)} maps "
                   f"réussies. Objectif sur chaque map : {criterion_text(CRITERIA[cat])}.")
        if not maps:
            st.caption("Pas encore de maps pour ce palier : clique sur Synchroniser.")
        for m in maps:
            map_card(conn, m["beatmap_id"], cat=cat, level_c=current, key=f"tier-{cat}")
        log = conn.execute("SELECT level_c, how FROM tier_log WHERE category = ? ORDER BY level_c DESC LIMIT 6",
                           (cat,)).fetchall()
        if log:
            st.caption("Paliers validés : " + " · ".join(f"{stars(r['level_c'] / 100)} ({r['how']})" for r in log))
        st.caption(f"Si ta note {LABELS[cat]} dépasse le palier en cours de {fmt.level(progression.AUTO_SKIP_MARGIN_C)} "
                   f"(avec au moins {progression.AUTO_SKIP_GAMES} parties), le palier est validé d'office.")

retired = db.get_meta(conn, "retired_tier_maps", [])
if retired:
    with st.expander(f"Maps de palier remplacées récemment ({len(retired)})", icon=":material/swap_horiz:"):
        st.caption("Une map est remplacée quand tu as très peu de chances de la finir (jauge de vie trop dure pour "
                   f"ton niveau) ou après {progression.STUCK_ATTEMPTS} essais sans atteindre l'objectif. "
                   "Une map réussie n'est jamais retirée.")
        for m in reversed(retired[-8:]):
            info = map_info(conn, m["id"])
            st.markdown(f"- **{info['title']}** [{info['version']}] ({skill_label(m['category'])} "
                        f"{stars(m['level_c'] / 100)}) : {m['reason']}.")


def _unignore(beatmap_id: int) -> None:
    c = db.connect()
    progression.unignore_map(c, beatmap_id)
    db.del_meta(c, "session_key")
    c.commit()
    c.close()


ignored = db.get_meta(conn, "ignored_maps", [])
if ignored:
    with st.expander(f"Maps écartées à ta demande ({len(ignored)})", icon=":material/block:"):
        st.caption("Elles ne reviennent ni dans le programme du jour ni dans les paliers. Une map de palier écartée "
                   "est remplacée à la synchronisation suivante.")
        for m in reversed(ignored):
            info = map_info(conn, m["id"])
            with st.container(horizontal=True, vertical_alignment="center"):
                st.markdown(f"**{info['title']}** [{info['version']}] · {stars(info['stars'])}")
                st.button("Remettre", key=f"unignore-{m['id']}", icon=":material/undo:", type="tertiary",
                          on_click=_unignore, args=(m["id"],))
