"""Page « Guide » : comment se servir de l'outil, et ce que veulent dire les chiffres."""
import pandas as pd
import streamlit as st

from osuprog import db, fmt, goals, model, planner, ppcalc, progression
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
    "2. Vise l'**objectif** indiqué sous chaque map (le plus souvent : ta précision habituelle sur la map).\n"
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

st.subheader("Les pp")
with st.expander("D'où viennent les chiffres"):
    exact = ppcalc.engine_name() == ppcalc.LIVE
    st.markdown(
        "Les pp et les étoiles sont calculés avec **le code officiel d'osu!** : les mêmes valeurs que ton profil et "
        "que le classement « Live » de pp.huismetbenen.nl, détail Aim / Tap / Acc / Reading compris. Le **total** "
        "est celui de ton profil osu!. Chaque compétence classe tes parties selon ses propres pp, puis compte la "
        "1re en entier, la 2e à 95 %, la 3e à 90 %… Le **bonus** vient du nombre de maps jouées, pas de ton niveau."
        + ("" if exact else "\n\nPour l'instant le calcul est **approché** (rosu-pp, sans Reading) : il faut le "
                            "runtime .NET 8. Relance `lancer.bat`, il l'installe tout seul (environ 30 Mo)."))
with st.expander("La recherche « Objectif »"):
    st.markdown(
        "Elle parcourt **toutes les maps classées** (ranked et approved, les loved ne rapportent pas de pp) et garde "
        "celles où un full combo à ta précision atteint l'objectif. Les premières sont les plus accessibles : peu de "
        "miss attendus (d'après tes parties, selon les étoiles et la longueur), assez de chances de les finir (HP "
        "compris). Survole une map pour voir ses pp avec 1 ou 3 miss et ce que gagnerait ton total.")
with st.expander("La recherche « Similaires »"):
    st.markdown(
        "Choisis une map que tu aimes : elle cherche des maps classées **que tu n'as pas encore jouées**, à ±0,5★, "
        "de durée et de drain de vie proches. Elle compare l'aim, la vitesse, l'AR, la densité, puis **les sauts et "
        "le rythme** mesurés dans les fichiers (la première recherche sur une map prend quelques secondes). Les pp "
        "affichés sont ceux d'un bon run à toi : une estimation, pas une promesse. La musique n'est pas comparée.")

st.subheader("La séance du jour")
with st.expander("Comment les maps sont choisies"):
    st.markdown(
        f"Deux sortes de travail. **Entraînement** : des maps que tu finis environ {fmt.share(finish)} du temps, avec "
        "pour objectif **ta précision habituelle sur la map** (calculée sur tes parties récentes : étoiles, jauge de "
        "vie, OD, durée, et ce que tu fais d'habitude sur la map). Un objectif fixe (80 %) envoyait vers des maps "
        "bien plus faciles que celles que tu joues. **Montée** (environ "
        f"{fmt.share(planner.CLIMB_SHARE)} de la séance) : des maps près de ta limite, que tu finis proprement "
        f"(au plus 8 % de miss) environ {fmt.share(planner.CLIMB_FINISH)} du temps, de préférence celles qui rapportent des pp. Le temps est réparti "
        "entre les compétences selon leur retard (comparées à critère égal : 80 % de précision partout), leur place "
        "dans les maps du niveau suivant et l'incertitude de leur note (aucune ne dépasse "
        f"{fmt.share(planner.MAX_SHARE)}). Si tu as un **projet**, une partie de la séance le prépare. Seules les "
        "maps du programme comptent dans la durée de la séance : jouer autre chose ne vide pas ta liste. Ordre : "
        "échauffement, travail ciblé, montée, préparation et projet (ou défi), révision.")
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
        "- **Entraînement** : du travail ciblé à ton niveau. Objectif : au moins ta précision habituelle sur la "
        "map.\n"
        "- **Montée** : près de ta limite (tu la finis proprement environ 6 fois sur 10). Environ un tiers de la "
        "séance : c'est là que ton niveau de passe et tes pp montent. Le but : la finir avec au plus 8 % de miss, "
        "précision libre. Survivre ne suffit pas : sur une map à jauge de vie très basse, on finit même en ratant "
        "tout.\n"
        "- **Test de niveau** : mesure une compétence encore mal connue.\n"
        "- **Défi** : au-dessus de ton niveau, la rater est normal (les jours sans projet).\n"
        "- **Projet** : une map que tu t'acharnes à passer (au moins 5 essais sur 2 jours), repérée toute seule. "
        "Étape 1 : la finir ; étape 2 : la finir avec au plus 8 % de miss. Tes progrès sont en bas d'**Aujourd'hui**.\n"
        "- **Préparation** : même style que ton projet (sauts, rythme, format), un peu plus facile.\n"
        "- **Révision** : entretient une compétence peu travaillée.\n"
        "- **Examen** : pour passer au niveau suivant.")
with st.expander("Les chances affichées sont-elles justes ?"):
    record = goals.track_record(conn)
    offset = db.get_meta(conn, "session_offset") or {}
    if record["n"] >= 10:
        gap = record["met"] - record["shown"]
        verdict = ("C'est juste." if abs(gap) < 0.08 else
                   "Les chances étaient trop prudentes." if gap > 0 else "Les chances étaient trop optimistes.")
        st.markdown(
            f"Sur tes {record['n']} derniers premiers essais de maps du programme, les chances affichées étaient en "
            f"moyenne de **{fmt.share(record['shown'])}**, et tu as réussi **{fmt.share(record['met'])}** du temps. "
            f"{verdict}\n\nLes maps du programme sont plus faciles que leurs étoiles ne le disent (populaires, dans "
            "ta zone de confort) : à chaque synchronisation, l'outil apprend de tes séances de combien corriger ses "
            "chances" + (f" (correction actuelle : {fmt.share(model.corrected(0.5, offset.get('d', 0.0)))} au lieu de 50 %)"
                         if offset.get("d") else "") + ". À niveau égal, ta précision varie d'environ ±6 points "
            "d'une partie à l'autre : une chance reste une moyenne, pas une promesse.")
    else:
        st.markdown("Pas encore assez de maps du programme jouées pour juger.")
with st.expander("Une map ne me plaît pas"):
    st.markdown("Le menu **…** d'une map propose **Ne plus me proposer** (annulable dans Progression, « Maps "
                "retirées »). **Réglages → Proposer d'autres maps** change toute la liste du jour.")

st.subheader("Synchronisation")
with st.expander("Quelles parties sont prises en compte"):
    st.markdown(
        "En ligne : tes meilleurs scores et tes parties des dernières 24 h (fails compris). Dans lazer sur ce PC : "
        "toutes tes parties terminées. La synchro automatique (toutes les heures) et le suivi en direct évitent de "
        "perdre des fails.\n\nNe comptent pas : une partie quittée avec très peu de miss (Échap, relance), et un "
        "fail dans les premières notes d'une map bien plus facile que ton niveau (partie pour rire).")
with st.expander("La jauge de vie (HP)"):
    hp_k = (clear or {}).get("hp_k")
    st.markdown(
        "Les étoiles ignorent la jauge de vie : une map à HP élevé est plus dure à finir que ses étoiles ne le "
        "disent." + (f" Sur tes parties, chaque point de HP au-dessus de 4 vaut environ {stars(hp_k)} de plus pour "
                     "finir la map." if hp_k else "")
        + " L'outil en tient compte pour choisir tes maps.")
