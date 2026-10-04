"""Page « Guide » : comment se servir de l'outil, et ce que veulent dire les chiffres."""
from datetime import date

import pandas as pd
import streamlit as st

from osuprog import db, fmt, planner, progression
from osuprog.rating import load as load_ratings
from osuprog.skills import CATEGORIES, CRITERIA, LABELS
from osuprog.ui import SKILL_PLAIN, criterion_text, stars

conn = db.connect()
ratings = load_ratings(conn)
clear = db.get_meta(conn, "clear_level")
finish, meet = planner.INTENSITY[planner.DEFAULT_INTENSITY]

st.title("Guide")
st.markdown(
    "1. Ouvre **Aujourd'hui** et joue les maps **dans l'ordre**.\n"
    "2. Vise l'**objectif** indiqué sous chaque map (précision et miss maximum).\n"
    "3. C'est tout : avec le suivi en direct, les maps se cochent toutes seules à la fin de chaque partie."
)

st.subheader("Les chiffres")
with st.expander("Ma note dans une compétence"):
    st.markdown(
        "La difficulté (en étoiles) où tu atteins l'objectif de la compétence **une fois sur deux**. "
        "Chaque partie la fait monter si tu fais mieux que prévu, baisser sinon. Une étiquette « point fort » ou "
        "« point faible » n'apparaît que si l'écart dépasse la marge d'erreur des notes."
        + (f"\n\nTu **finis** des maps jusqu'à {stars(clear['level'])}, mais tes notes sont plus basses : il faut "
           "aussi les jouer **proprement**. Rapprocher les deux, c'est le but." if clear else ""))
with st.expander("Les 6 compétences et leurs objectifs"):
    for c in CATEGORIES:
        st.markdown(f"- **{LABELS[c]}** ({stars(ratings[c]['rating'])}) : {SKILL_PLAIN[c]}. "
                    f"Objectif : {criterion_text(CRITERIA[c])}.")
with st.expander("Paliers, niveau et examen"):
    st.markdown(
        f"Un palier tous les {fmt.level(progression.STEP_C)} par compétence. Réussis ses "
        f"{progression.MAPS_PER_TIER} maps pour le valider. Quand les 6 compétences ont atteint le niveau suivant, "
        f"un examen de {progression.EXAM_MAPS} maps ({criterion_text(progression.EXAM_CRITERION)}) le débloque. "
        f"Une compétence dont la note dépasse le palier de {fmt.level(progression.AUTO_SKIP_MARGIN_C)} le valide "
        "d'office.")
with st.expander("Précision au clic (UR)"):
    st.markdown(
        "L'outil rejoue tes replays de lazer contre les notes de la map. **UR** (unstable rate) : la régularité de "
        "tes clics, plus c'est bas mieux c'est. **Tendance** : si tu tapes en moyenne trop tôt ou trop tard (si "
        "c'est le cas sur toutes les maps, règle le décalage audio de lazer). **Miss de visée** : cliqué au bon "
        "moment mais à côté ; les autres miss sont des clics trop tôt ou trop tard.")

st.subheader("La séance du jour")
with st.expander("Comment les maps sont choisies"):
    st.markdown(
        f"Des maps que tu finis environ {fmt.share(finish)} du temps, avec {fmt.share(meet)} de chances "
        "d'atteindre l'objectif : assez de réussites pour progresser, assez d'erreurs pour apprendre. Le temps est "
        "réparti entre les compétences selon leur retard, leur place dans les maps du niveau suivant et l'incertitude "
        f"de leur note (aucune ne dépasse {fmt.share(planner.MAX_SHARE)}). Ordre : échauffement, travail ciblé, défi, "
        "révision.")
    summary = db.get_meta(conn, "plan_summary") or {}
    shares, parts = summary.get("shares") or {}, summary.get("parts") or {}
    if shares:
        st.dataframe(pd.DataFrame([
            {"Compétence": LABELS[s], "Part aujourd'hui": shares[s], "Note": stars(ratings[s]["rating"]),
             "Retard": f"{parts[s]['gap']:+.2f}★".replace(".", ",")}
            for s in sorted(shares, key=shares.get, reverse=True) if s in LABELS
        ]), hide_index=True, width="stretch",
            column_config={"Part aujourd'hui": st.column_config.ProgressColumn(format="percent", min_value=0,
                                                                                max_value=1)})
with st.expander("Les types de maps"):
    st.markdown(
        "- **Échauffement** : facile, pour te mettre en route.\n"
        "- **Objectif** : une map de ton palier en cours.\n"
        "- **Entraînement** : du travail ciblé.\n"
        "- **Test de niveau** : mesure une compétence encore mal connue.\n"
        "- **Défi** : au-dessus de ton niveau, la rater est normal.\n"
        "- **Révision** : entretient une compétence peu travaillée.\n"
        "- **Examen** : pour passer au niveau suivant.")
with st.expander("Les prévisions sont-elles justes ?"):
    check = planner.forecast_check(conn, date.today().isoformat())
    if check["n"] >= 5:
        st.markdown(f"Ces 14 derniers jours, sur {check['n']} maps proposées, tu as atteint l'objectif **dès le "
                    f"premier essai** {check['met']} fois ; l'outil en prévoyait {check['predicted']:.0f}. "
                    "Il se recale tout seul à chaque synchronisation.")
    else:
        st.markdown("Pas encore assez de maps jouées pour juger.")
with st.expander("Une map ne me plaît pas"):
    st.markdown("Le menu **…** d'une map propose **Ne plus me proposer** (annulable dans Progression, « Maps "
                "retirées »). **Réglages → Proposer d'autres maps** change toute la liste du jour.")

st.subheader("Synchronisation")
with st.expander("Quelles parties sont prises en compte"):
    st.markdown(
        "En ligne : tes meilleurs scores et tes parties des dernières 24 h (fails compris). Dans lazer sur ce PC : "
        "toutes tes parties terminées. La synchro automatique (toutes les heures) et le suivi en direct évitent de "
        "perdre des fails.")
with st.expander("La jauge de vie (HP)"):
    hp_k = (clear or {}).get("hp_k")
    st.markdown(
        "Les étoiles ignorent la jauge de vie : une map à HP élevé est plus dure à finir que ses étoiles ne le "
        "disent." + (f" Sur tes parties, chaque point de HP au-dessus de 4 vaut environ {stars(hp_k)} de plus pour "
                     "finir la map." if hp_k else "")
        + " L'outil en tient compte pour choisir tes maps.")
