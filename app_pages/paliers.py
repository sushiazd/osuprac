"""Page « Progression » : niveau validé, paliers par compétence et examen."""
import streamlit as st

from osuprog import db, progression
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

st.title("Ta progression", icon=":material/stairs:")
st.markdown(
    "Chaque compétence a une **échelle de paliers**, un tous les 0,25★. Pour valider un palier, réussis "
    "ses **3 maps** en atteignant l'objectif de la compétence. Ton **niveau validé** avance de 0,5★ "
    "quand **toutes** tes compétences ont atteint le niveau suivant et que tu as réussi l'examen."
)

with st.container(horizontal=True):
    st.metric("Niveau validé", stars(rank / 100), border=True,
              help="Le niveau que tu as prouvé dans les 6 compétences à la fois.")
    st.metric("Prochain niveau", stars(target / 100), border=True)
    st.metric("Compétences prêtes pour ce niveau", f"{len(ready)}/{len(CATEGORIES)}", border=True)

with st.container(border=True):
    st.markdown(f"**Pour atteindre le niveau {stars(target / 100)} :**")
    with st.container(horizontal=True, gap="small"):
        for cat in CATEGORIES:
            if cat in ready:
                st.badge(LABELS[cat], icon=":material/check_circle:", color="green")
            else:
                st.badge(f"{LABELS[cat]} : palier {stars((progress.get(cat, 0) + STEP_C) / 100)} en cours",
                         icon=":material/radio_button_unchecked:", color="gray")
    if exam["unlocked"]:
        st.markdown(f"Toutes tes compétences sont prêtes : réussis l'**examen** ci-dessous "
                    f"({criterion_text(progression.EXAM_CRITERION)} sur chaque map).")
        maps = conn.execute("SELECT beatmap_id FROM tier_maps WHERE category = 'exam' AND level_c = ? ORDER BY slot",
                            (target,)).fetchall()
        for m in maps:
            map_card(conn, m["beatmap_id"], cat="exam", level_c=target)
    else:
        st.caption(f"Valide le palier {stars(target / 100)} dans chaque compétence ; l'examen se débloquera ensuite.")

st.subheader("Paliers par compétence", icon=":material/flag:")
tabs = st.tabs([f"{LABELS[c]} · {stars((progress.get(c, 0) + STEP_C) / 100)}" for c in CATEGORIES])
for tab, cat in zip(tabs, CATEGORIES):
    with tab:
        current = progress.get(cat, 0) + STEP_C
        with st.container(horizontal=True, gap="small", vertical_alignment="center"):
            for level in range(max(STEP_C, current - 2 * STEP_C), current + 3 * STEP_C, STEP_C):
                if level < current:
                    st.badge(stars(level / 100), icon=":material/check_circle:", color="green")
                elif level == current:
                    st.badge(f"{stars(level / 100)} en cours", icon=":material/flag:", color="primary")
                else:
                    st.badge(stars(level / 100), icon=":material/lock:", color="gray")
        maps = conn.execute("SELECT beatmap_id FROM tier_maps WHERE category = ? AND level_c = ? ORDER BY slot",
                            (cat, current)).fetchall()
        met = sum(1 for m in maps if (r := progression.best_result(conn, m["beatmap_id"], CRITERIA[cat])) and r["met"])
        st.markdown(f"{SKILL_ICONS[cat]} **Palier {stars(current / 100)}** : {met}/{len(maps)} maps réussies. "
                    f"Objectif sur chaque map : {criterion_text(CRITERIA[cat])}.")
        st.progress(met / len(maps) if maps else 0.0)
        if not maps:
            st.caption("Pas encore de maps pour ce palier : clique sur Synchroniser.")
        for m in maps:
            map_card(conn, m["beatmap_id"], cat=cat, level_c=current)
        log = conn.execute("SELECT level_c, how FROM tier_log WHERE category = ? ORDER BY level_c DESC LIMIT 6",
                           (cat,)).fetchall()
        if log:
            st.caption("Paliers validés : " + " · ".join(f"{stars(r['level_c'] / 100)} ({r['how']})" for r in log))
        st.caption(f"Si ta note {LABELS[cat]} dépasse le palier en cours de 0,5★ (avec au moins "
                   f"{progression.AUTO_SKIP_GAMES} parties), le palier est validé d'office.")

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
