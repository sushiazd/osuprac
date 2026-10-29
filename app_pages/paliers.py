"""Page « Progression » : niveau, paliers par compétence et succès."""
from datetime import date

import streamlit as st

from osuprog import db, progression, rewards, visuals
from osuprog.progression import STEP_C
from osuprog.skills import CATEGORIES, CRITERIA, LABELS
from osuprog.ui import map_card, map_info, requirement_line, short_goal, skill_label, stars

conn = db.connect()
today = date.today().isoformat()
exam = progression.exam_state(conn)
progress = progression.load_progress(conn)
rank, target = exam["rank_c"], exam["target_c"]
ready = [c for c in CATEGORIES if progress.get(c, 0) >= target]


def tier_maps(cat: str, level_c: int) -> list:
    return conn.execute("SELECT beatmap_id FROM tier_maps WHERE category = ? AND level_c = ? ORDER BY slot",
                        (cat, level_c)).fetchall()


def met_count(cat: str, maps: list) -> int:
    return sum(1 for m in maps if (r := progression.best_result(conn, m["beatmap_id"], CRITERIA[cat])) and r["met"])


def _unignore(beatmap_id: int) -> None:
    c = db.connect()
    progression.unignore_map(c, beatmap_id)  # la séance du jour s'ajuste toute seule (sans tout retirer au sort)
    c.close()


st.title("Progression")
st.html(visuals.summary([(stars(rank / 100), "niveau validé"), (stars(target / 100), "prochain niveau"),
                         (f"{len(ready)}/{len(CATEGORIES)}", "compétences prêtes")]))
st.html(visuals.progress(len(ready) / len(CATEGORIES)))
st.space("small")

paliers_tab, succes_tab = st.tabs(["Paliers", "Succès"])

with paliers_tab:
    st.caption(f"Réussis les {progression.MAPS_PER_TIER} maps d'un palier pour passer au suivant. Quand les 6 "
               f"compétences atteignent {stars(target / 100)}, un examen débloque le niveau.")
    rows = []
    for cat in CATEGORIES:
        current = progress.get(cat, 0) + STEP_C
        maps = tier_maps(cat, current)
        rows.append({"label": LABELS[cat], "current_c": current, "step_c": STEP_C, "met": met_count(cat, maps),
                     "needed": max(len(maps), progression.MAPS_PER_TIER)})
    st.html(visuals.tier_tracks(rows))

    if exam["unlocked"]:
        st.subheader(f"Examen du niveau {stars(target / 100)}")
        st.markdown(f"Pour réussir l'examen : **{len(tier_maps('exam', target))} maps**, chacune en une seule partie "
                    f"finie avec {short_goal(progression.EXAM_CRITERION, None)}.")
        for m in tier_maps("exam", target):
            map_card(conn, m["beatmap_id"], cat="exam", level_c=target, key="exam")
            st.markdown(requirement_line(conn, m["beatmap_id"], progression.EXAM_CRITERION))

    st.subheader("Maps du palier")
    chosen = st.segmented_control("Compétence", [LABELS[c] for c in CATEGORIES], default=LABELS[CATEGORIES[0]],
                                  label_visibility="collapsed", key="tier_skill") or LABELS[CATEGORIES[0]]
    cat = next(c for c in CATEGORIES if LABELS[c] == chosen)
    current = progress.get(cat, 0) + STEP_C
    maps = tier_maps(cat, current)
    crit = CRITERIA[cat]
    st.markdown(f"**Pour valider le palier {stars(current / 100)} en {LABELS[cat]}** : réussis les "
                f"**{progression.MAPS_PER_TIER} maps** ci-dessous ({met_count(cat, maps)}/{progression.MAPS_PER_TIER} "
                f"faites). Sur chacune, il faut **une seule partie finie** avec au moins **{crit.min_acc:g} % de "
                f"précision** et au plus **{str(crit.max_miss_pct).rstrip('0').rstrip('.').replace('.', ',')} % de "
                f"miss** (le nombre de miss autorisés dépend de la longueur de la map).")
    if not maps:
        st.caption("Pas encore de maps pour ce palier : clique sur Synchroniser.")
    for m in maps:
        map_card(conn, m["beatmap_id"], cat=cat, level_c=current, key=f"tier-{cat}")
        st.markdown(requirement_line(conn, m["beatmap_id"], crit))

    retired = db.get_meta(conn, "retired_tier_maps", [])
    ignored = db.get_meta(conn, "ignored_maps", [])
    if retired or ignored:
        with st.expander("Maps retirées"):
            for m in reversed(ignored):
                info = map_info(conn, m["id"])
                with st.container(horizontal=True, vertical_alignment="center"):
                    st.markdown(f"{info['title']} · {info['version']}  \n:gray[écartée à ta demande]")
                    st.button("Remettre", key=f"unignore-{m['id']}", icon=":material/undo:", type="tertiary",
                              on_click=_unignore, args=(m["id"],))
            for m in reversed(retired[-8:]):
                if m["reason"] == "écartée à ta demande":
                    continue
                info = map_info(conn, m["id"])
                st.markdown(f"{info['title']} · {info['version']}  \n:gray[{skill_label(m['category'])} "
                            f"{stars(m['level_c'] / 100)} · {m['reason']}]")


with succes_tab:
    streak = rewards.streaks(rewards.play_days(conn), today)
    week = rewards.week_summary(conn, today)
    achievements = rewards.achievements(conn, today)
    unlocked = sorted((a for a in achievements if a.unlocked), key=lambda a: a.unlocked, reverse=True)
    next_up = sorted((a for a in achievements if not a.unlocked), key=lambda a: -a.progress)

    with st.container(horizontal=True):
        st.metric("Jours de suite", streak["current"], border=True, help=f"Record : {streak['best']} jours.")
        st.metric("Parties cette semaine", week["plays"], border=True)
        st.metric("Paliers cette semaine", week["tiers"], border=True)
        st.metric("Succès", f"{len(unlocked)}/{len(achievements)}", border=True)

    def as_badge(a) -> dict:
        return {"symbol": a.icon.removeprefix(":material/").rstrip(":"), "title": a.title, "text": a.text,
                "unlocked": date.fromisoformat(a.unlocked).strftime("%d/%m/%Y") if a.unlocked else None,
                "progress": a.progress}

    st.subheader("Prochains")
    st.html(visuals.badges([as_badge(a) for a in next_up[:6]]))
    st.subheader("Débloqués")
    st.html(visuals.badges([as_badge(a) for a in unlocked]))
