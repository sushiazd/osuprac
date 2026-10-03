"""Page « Aujourd'hui » : la séance du jour, dans l'ordre, avec pourquoi, objectif, chances et durée."""
from datetime import date

import pandas as pd
import streamlit as st

from osuprog import db, fmt, model, planner, progression
from osuprog.api import MissingCredentials, OsuApi
from osuprog.rating import load as load_ratings
from osuprog.skills import LABELS
from osuprog.ui import SKILL_PLAIN, item_state, map_card, stars

conn = db.connect()
ratings = load_ratings(conn)
day = date.today().isoformat()
INTENSITY_LABELS = {"confort": "Confort", "équilibré": "Équilibré", "intense": "Intense"}
INTENSITY_HELP = {
    "confort": "Maps que tu finis presque toujours : pour consolider.",
    "équilibré": "Maps que tu finis la plupart du temps, avec un objectif jouable : le bon compromis.",
    "intense": "Maps plus dures, plus d'échecs : pour progresser plus vite si tu as la forme.",
}


def api_or_none():
    try:
        return OsuApi()
    except MissingCredentials:
        return None


# --- Réglages : durée de la séance et intensité ------------------------------------------------
cfg = planner.settings(conn)
with st.popover("Régler ma séance", icon=":material/tune:"):
    duration_options = ["Auto", "30 min", "45 min", "60 min", "90 min"]
    current_duration = "Auto" if cfg["auto_minutes"] else f"{cfg['minutes']} min"
    chosen_duration = st.segmented_control(
        "Durée", duration_options, default=current_duration if current_duration in duration_options else "Auto",
        help=f"« Auto » reprend la durée de tes séances habituelles (≈ {planner.typical_session_minutes(conn)} min).")
    chosen_intensity = st.segmented_control(
        "Intensité", list(INTENSITY_LABELS.values()), default=INTENSITY_LABELS[cfg["intensity"]],
        help="\n".join(f"{INTENSITY_LABELS[k]} : {v}" for k, v in INTENSITY_HELP.items()))
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

# Nouveau jour ou nouveaux réglages sans synchro : on prépare quand même la séance depuis le catalogue local.
planner.build_session(conn, None, ratings, day, log=lambda *_: None)
plan = conn.execute("SELECT * FROM session_plan WHERE day = ? ORDER BY pos", (day,)).fetchall()
summary = db.get_meta(conn, "plan_summary") or {}
shares = summary.get("shares") or {}
parts = summary.get("parts") or {}
exam = progression.exam_state(conn)
cfg = planner.settings(conn)

items = []
for row in plan:
    state, _ = item_state(conn, row["role"], row["category"], row["beatmap_id"], day)
    items.append({**dict(row), "state": state})
played = sum(i["state"] != "todo" for i in items)
goals = [i for i in items if i["role"] in ("Objectif", "Examen")]
goals_met = sum(i["state"] == "done" for i in goals)
total_minutes = sum(i["minutes"] or 0 for i in items)
ranked = sorted(shares, key=shares.get, reverse=True)
top = [s for s in ranked if s in LABELS][:3]

st.title("Ton programme du jour", icon=":material/today:")
if top:
    focus = ", ".join(f"**{LABELS[s]}** ({shares[s]:.0%})" for s in top)
    st.markdown(
        f"Séance de **≈ {total_minutes:.0f} min** ({len(items)} maps), construite pour te faire progresser le plus vite "
        f"possible. On travaille surtout {focus}. Joue les maps **dans l'ordre** ; après ta session, clique sur "
        f"**Synchroniser** : les maps se cochent toutes seules et la suite du programme s'ajuste."
    )

clear = db.get_meta(conn, "clear_level")
with st.container(horizontal=True):
    st.metric("Maps jouées aujourd'hui", f"{played}/{len(items)}", border=True,
              help="Une map d'objectif compte comme finie quand tu atteins son objectif.")
    if top:
        gap = model.skill_gaps({s: ratings[s]["rating"] for s in ratings}, {s: ratings[s]["games"] for s in ratings})[top[0]]
        certainty = ("Elle est nettement sous tes autres compétences." if gap["significant"] and gap["gap"] < 0 else
                     "Son retard est dans la marge d'erreur : elle est prioritaire surtout parce qu'elle compte "
                     "beaucoup dans les maps du niveau suivant, ou parce que sa note est encore incertaine.")
        st.metric("Priorité n°1", LABELS[top[0]], border=True,
                  help=f"{LABELS[top[0]]}, c'est {SKILL_PLAIN[top[0]]}. Ta note : {stars(ratings[top[0]]['rating'])}. "
                       + certainty)
    if clear:
        st.metric("Tu réussis jusqu'à", stars(clear["level"]), border=True,
                  help=f"À {stars(clear['level'])}, tu finis la map (sans fail) une fois sur deux, sur une map à jauge de "
                       f"vie normale (HP 4). Chaque point de HP en plus vaut environ "
                       f"{clear.get('hp_k', 0):.2f}★ de difficulté pour la finir. "
                       f"Ta plus haute map réussie : {stars(clear['highest'])}.")
    st.metric("Niveau validé", stars(exam["rank_c"] / 100), border=True,
              help=f"Le niveau où tu fais une bonne précision dans les 6 compétences. "
                   f"Prochain niveau : {stars(exam['target_c'] / 100)} (onglet Progression).")
st.progress(played / len(items) if items else 0.0,
            text=f"{played}/{len(items)} maps jouées · {goals_met}/{len(goals)} objectifs réussis")


def card(i, *, highlight=False):
    map_card(conn, i["beatmap_id"], role=i["role"], cat=i["category"], level_c=i["level_c"],
             number=items.index(i) + 1, day=day, highlight=highlight,
             reason=i["reason"], p=i["p"], minutes=i["minutes"],
             ignorable=i["role"] != "Examen" and i["state"] == "todo")


todo = [i for i in items if i["state"] == "todo"]
retry = [i for i in items if i["state"] == "retry"]
finished = [i for i in items if i["state"] == "done"]
current = (todo or retry or [None])[0]

if not items:
    st.info("Pas encore de maps pour aujourd'hui : clique sur **Synchroniser** à gauche.", icon=":material/sync:")
elif current is None:
    st.success("Séance terminée, bravo ! Le temps prévu est écoulé : mieux vaut t'arrêter là que jouer fatigué. "
               "Si tu as encore de l'énergie, tu peux rajouter un peu de temps.",
               icon=":material/emoji_events:")
    if st.button("Rajouter 15 min", icon=":material/more_time:"):
        planner.add_session_time(conn, day, 15)
        with st.spinner("Choix des maps…"):
            planner.build_session(conn, api_or_none(), ratings, day, log=lambda *_: None)
        st.rerun()
else:
    st.subheader("À jouer maintenant", icon=":material/play_arrow:")
    card(current, highlight=True)
    st.caption("« Ouvrir dans osu! » affiche la map dans lazer, qui la télécharge si besoin. Avec le **suivi en "
               "direct** (barre de gauche), elle se coche toute seule à la fin de ta partie.")

rest = [i for i in todo + retry if i is not current]
if rest:
    st.subheader("Ensuite", icon=":material/format_list_numbered:")
    for i in rest:
        card(i)

if finished:
    with st.expander(f"Déjà faites aujourd'hui ({len(finished)})", icon=":material/check_circle:"):
        for i in finished:
            card(i)

# --- Pourquoi ce programme ---------------------------------------------------------------------
if shares:
    with st.expander("Pourquoi ce programme ?", icon=":material/psychology:"):
        st.markdown(
            "Chaque jour, l'outil répartit ton temps entre les compétences d'après trois critères : "
            "**le retard** de la compétence sur tes autres (plus elle est basse, plus elle compte), "
            "**sa présence** dans les maps que tu voudras jouer ensuite, et **l'incertitude** de sa note "
            "(une compétence mal mesurée est plus travaillée pour être située). "
            f"Aucune ne dépasse {fmt.share(planner.MAX_SHARE)} : varier les compétences fait mieux retenir."
        )
        table = pd.DataFrame([
            {
                "Compétence": LABELS[s],
                "Part du programme": shares[s],
                "Ta note": stars(ratings[s]["rating"]),
                "Retard sur tes autres compétences": f"{parts[s]['gap']:+.2f}★".replace(".", ","),
                "Présence dans les maps du niveau suivant": parts[s]["importance"],
                "Précision de la note": "±" + f"{parts[s]['sd']:.2f}★".replace(".", ","),
            }
            for s in sorted(shares, key=shares.get, reverse=True) if s in LABELS
        ])
        st.dataframe(
            table, hide_index=True, width="stretch",
            column_config={
                "Part du programme": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
                "Présence dans les maps du niveau suivant": st.column_config.ProgressColumn(
                    format="percent", min_value=0, max_value=1),
            },
        )
        default_finish, default_meet = planner.INTENSITY[planner.DEFAULT_INTENSITY]
        st.markdown(
            f"**Difficulté des maps :** visée = des maps que tu finis environ "
            f"**{summary.get('target_finish', default_finish):.0%}** du temps, avec "
            f"**{summary.get('target_meet', default_meet):.0%}** "
            "de chances d'atteindre l'objectif de précision. Assez de réussites pour progresser, assez d'erreurs "
            "pour apprendre. Tu peux changer ça avec « Régler ma séance » (intensité).\n\n"
            "**Ordre de la séance :** échauffement facile → travail ciblé (du plus accessible au plus dur) → "
            "défi juste au-dessus de ton niveau de réussite → révision d'une compétence peu travaillée."
        )

# --- Efficacité : le programme tient-il ses promesses ? ---------------------------------------
check = planner.forecast_check(conn, day)
if check["any_rows"]:
    with st.expander("Le programme fonctionne-t-il ? Ses prévisions des 14 derniers jours", icon=":material/fact_check:"):
        n_played, done, pred = check["n"], check["met"], check["predicted"]
        if n_played >= 5:
            st.markdown(
                f"Sur **{n_played}** maps proposées que tu as jouées, tu as atteint l'objectif **dès le premier essai "
                f"{done}** fois ; l'outil en prévoyait environ **{pred:.0f}**. "
                + ("Les prévisions sont justes : la difficulté est bien calibrée." if abs(done - pred) <= max(2, 0.25 * pred)
                   else "L'écart est notable : l'outil se recale automatiquement à chaque synchronisation.")
            )
            st.caption("Seul le premier essai du jour compte : les chances affichées sont celles d'un essai, et "
                       "compter les réussites après plusieurs essais ferait paraître l'outil plus juste qu'il ne l'est.")
        else:
            st.caption("Pas encore assez de maps jouées sur les jours précédents pour juger les prévisions.")

if st.button("Changer les maps du jour", icon=":material/refresh:",
             help="Propose d'autres maps avec la même logique. Celles que tu as déjà jouées restent en tête."):
    planner.reset_session(conn, day)
    with st.spinner("Choix des maps…"):
        planner.build_session(conn, api_or_none(), ratings, day, log=lambda *_: None)
    st.rerun()
