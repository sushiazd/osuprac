"""Page « Guide » : tout ce que veulent dire les chiffres, en clair."""
import streamlit as st

from osuprog import db, fmt, planner, progression
from osuprog.rating import load as load_ratings
from osuprog.skills import CATEGORIES, CRITERIA, LABELS
from osuprog.ui import SKILL_ICONS, SKILL_PLAIN, criterion_text, stars

conn = db.connect()
ratings = load_ratings(conn)
rank = db.get_meta(conn, "rank_c", 0)
finish, meet = planner.INTENSITY[planner.DEFAULT_INTENSITY]
max_share = fmt.share(planner.MAX_SHARE)

st.title("Guide", icon=":material/help:")
st.markdown("Tout ce que veulent dire les chiffres du tableau de bord, et comment t'en servir.")

with st.container(border=True):
    st.markdown("#### :material/rocket_launch: Ta routine en 3 étapes")
    st.markdown(
        "1. Ouvre l'onglet **Aujourd'hui** et joue les maps **dans l'ordre** (la première est en haut, en grand).\n"
        "2. Pour chaque map, regarde l'**objectif** : c'est la précision et le nombre de miss à atteindre.\n"
        "3. Après ta session, clique sur **Synchroniser** (ou laisse la synchro automatique le faire) : "
        "les maps réussies se cochent, tes notes et tes paliers se mettent à jour."
    )

with st.expander("Que veulent dire les étoiles (★) ?", icon=":material/star:"):
    st.markdown(
        "Les étoiles, c'est la difficulté d'une map calculée par osu! (le *star rating*). "
        "Une map 1★ est très facile, 3★ c'est du niveau *Hard*, 4-5★ *Insane*, 6★ et plus *Expert*. "
        "Dans l'outil, **tout est exprimé en étoiles** pour pouvoir comparer : ta note dans une compétence, "
        "tes paliers et ton niveau."
    )

clear = db.get_meta(conn, "clear_level")
acc_obj = CRITERIA["accuracy"]
with st.expander("C'est quoi ma note dans une compétence ?", icon=":material/insights:"):
    st.markdown(
        "C'est la difficulté de map (en étoiles) où tu atteins l'objectif de la compétence **environ une fois "
        f"sur deux**. Exemple : Accuracy à 3,00★ veut dire que sur une map orientée Accuracy de 3,00★, tu fais "
        f"{criterion_text(acc_obj)} environ une fois sur deux. Plus bas, tu y arrives presque "
        "toujours ; plus haut, rarement."
    )

with st.expander("Pourquoi mes notes sont plus basses que les maps que je réussis ?",
                 icon=":material/compare_arrows:"):
    reach = (f"Toi, tu réussis (sans fail) une map sur deux à **{stars(clear['level'])}**, et ta plus haute map "
             f"réussie est à {stars(clear['highest'])}. " if clear else "")
    st.markdown(
        "Parce que ce ne sont pas les mêmes questions :\n\n"
        f"- **Réussir une map** : la finir sans échouer. {reach}\n"
        "- **Ta note** : jusqu'où tu joues **proprement**, c'est-à-dire en atteignant l'objectif de "
        f"précision de la compétence (de {min(c.min_acc for c in CRITERIA.values()):g} à "
        f"{max(c.min_acc for c in CRITERIA.values()):g} % selon la compétence).\n\n"
        "Tu peux finir des maps plus dures que ce que tu joues proprement : l'écart entre les deux est justement "
        "ce que tu peux travailler. L'important n'est pas le chiffre exact, mais **l'écart entre tes "
        "compétences** : c'est lui qui dit quoi travailler."
    )

with st.expander("C'est quoi un palier ?", icon=":material/flag:"):
    st.markdown(
        f"Chaque compétence a une échelle de paliers tous les {fmt.level(progression.STEP_C)} (2,75 · 3,00 · 3,25 · "
        f"3,50…). Un palier = **{progression.MAPS_PER_TIER} maps** choisies pour cette compétence, autour de sa "
        f"difficulté. Réussis l'objectif sur les {progression.MAPS_PER_TIER} et "
        "le palier suivant se débloque. Tu peux les jouer dans n'importe quel ordre, autant de fois que tu veux. "
        f"Si ta note dépasse largement un palier ({fmt.level(progression.AUTO_SKIP_MARGIN_C)} au-dessus), il est "
        "validé d'office pour ne pas te "
        "faire perdre de temps."
    )

with st.expander("C'est quoi le niveau validé et l'examen ?", icon=":material/school:"):
    st.markdown(
        f"Ton **niveau validé** ({stars(rank / 100)} en ce moment) est le niveau que tu as prouvé **dans "
        f"toutes les compétences à la fois**. Il avance de {fmt.level(progression.RANK_STEP_C)} à la fois : quand "
        "tes 6 compétences ont validé le palier du niveau suivant, un **examen** de "
        f"{progression.EXAM_MAPS} maps polyvalentes se débloque "
        f"({criterion_text(progression.EXAM_CRITERION)} sur chaque). Réussis-le, et ton niveau monte. "
        "C'est volontairement exigeant : il empêche de monter en ne travaillant que ses points forts."
    )

with st.expander("Les 6 compétences", icon=":material/category:"):
    for cat in CATEGORIES:
        r = ratings.get(cat, {"rating": 0})
        st.markdown(f"{SKILL_ICONS[cat]} **{LABELS[cat]}** ({stars(r['rating'])}) : {SKILL_PLAIN[cat]}. "
                    f"Objectif sur ses maps : {criterion_text(CRITERIA[cat])}.")

with st.expander("Comment le programme du jour est construit ?", icon=":material/psychology:"):
    st.markdown(
        "Le programme est calculé pour te faire progresser **le plus vite possible**, en s'appuyant sur ce que "
        "l'on sait de l'apprentissage :\n\n"
        "1. **La bonne difficulté.** On progresse le mieux sur des maps que l'on finit la plupart du temps mais "
        f"où l'on fait encore des erreurs. L'outil vise des maps que tu finis environ {fmt.share(finish)} du temps, "
        f"avec {fmt.share(meet)} de chances d'atteindre l'objectif de précision. Ces chances sont calculées sur **tes** "
        "parties et "
        "recalibrées à chaque synchronisation.\n"
        "2. **Travailler là où ça rapporte le plus.** Chaque compétence reçoit une part du programme selon son "
        "retard sur tes autres compétences, sa présence dans les maps que tu voudras jouer ensuite, et "
        f"l'incertitude de sa note. Aucune ne dépasse {max_share}.\n"
        "3. **De la variété.** Enchaîner dix maps de la même compétence rapporte de moins en moins : le "
        "programme alterne, et ménage les compétences que tu viens de beaucoup solliciter.\n"
        f"4. **Répéter ce qu'on a raté.** Une map ratée récemment revient (jusqu'à {progression.STUCK_ATTEMPTS} "
        "essais), parce que rejouer "
        "fixe les passages difficiles ; une map déjà réussie est écartée quelques jours.\n"
        "5. **Une séance structurée dans ton temps.** Échauffement facile, travail ciblé, défi juste au-dessus "
        "de ton niveau de réussite, puis révision d'une compétence peu travaillée. La durée suit celle de tes "
        "séances habituelles (réglable).\n\n"
        "Ce que ça ne sait pas : si ta façon de progresser personnelle diffère de ces principes généraux. "
        "L'onglet *Aujourd'hui* te montre si les prévisions de l'outil tiennent sur tes dernières séances."
    )

with st.expander("Pourquoi une map à 3★ peut être plus dure que prévu : la jauge de vie (HP)",
                 icon=":material/favorite:"):
    hp_k = (db.get_meta(conn, "clear_level") or {}).get("hp_k")
    st.markdown(
        "Les étoiles ne tiennent pas compte de la **jauge de vie** (HP). Sur une map à HP élevé, chaque \"100\" "
        "ou \"50\" te coûte de la vie : tu peux échouer avec 80 % de précision et peu de miss. "
        + (f"Sur **tes** parties, chaque point de HP au-dessus de 4 rend une map aussi difficile à finir qu'environ "
           f"**{hp_k:.2f}★** de plus. ".replace(".", ",") if hp_k else "")
        + "L'outil en tient compte : il estime tes chances de finir chaque map, évite de te proposer des maps que tu "
        "ne finiras presque jamais, et remplace une map de palier sur laquelle tu es bloqué."
    )

with st.expander("Les types de maps du programme du jour", icon=":material/format_list_numbered:"):
    st.markdown(
        "- **Échauffement** : une map facile dans ton point fort.\n"
        "- **Test de niveau** : pour mesurer une compétence où tu as peu de parties. Pas de pression.\n"
        "- **Objectif** : une map de ton palier en cours, à réussir pour progresser.\n"
        "- **Entraînement** : une map choisie pour travailler une de tes compétences prioritaires.\n"
        "- **Examen** : pour passer au niveau suivant.\n"
        "- **Défi** : plus dur que ton niveau, rater est normal.\n"
        "- **Révision** : pour garder le niveau dans une autre compétence."
    )

with st.expander("Synchronisation : quelles parties sont prises en compte ?", icon=":material/sync:"):
    st.markdown(
        "- **En ligne :** ton meilleur score sur chaque map depuis ton inscription, le nombre de parties par "
        "map, et tes parties des dernières 24 h (fails compris).\n"
        "- **Dans lazer sur ce PC :** toutes tes parties terminées depuis l'installation de lazer.\n"
        "- **Perdu pour toujours :** les fails de plus de 24 h qu'osu! n'a pas gardés, et les parties quittées "
        "en cours.\n\n"
        "Avec la synchro automatique (toutes les heures), plus aucune partie envoyée à osu! n'est perdue. "
        "Le **suivi en direct** (barre de gauche) synchronise en plus à la fin de chaque partie dans lazer, "
        "tant que le tableau de bord est ouvert."
    )

with st.expander("Une map est trop dure, ou mal classée ?", icon=":material/tune:"):
    st.markdown(
        "Les maps de **défi** et les **tests de niveau** peuvent être trop dures : c'est prévu, joue-les "
        "quand même, le résultat sert à calibrer. Si les autres maps du jour ne te conviennent pas, le bouton "
        "**Changer les maps du jour** (en bas de l'onglet Aujourd'hui) en pioche d'autres, et **Ne plus me "
        "proposer** écarte une map pour de bon (annulable dans l'onglet Progression). Le classement des "
        "maps par compétence est automatique (d'après leurs sauts, streams, AR, OD et sliders) : il peut se "
        "tromper de temps en temps."
    )
