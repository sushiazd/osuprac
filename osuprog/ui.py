"""Éléments d'interface partagés par les pages du tableau de bord (textes en clair, cartes de map)."""
import json
from datetime import datetime
from pathlib import Path

import streamlit as st

from . import db, planner, progression, visuals
from .fmt import num, pct, stars  # noqa: F401  (réexportés pour les pages)
from .rating import HP_REF
from .skills import CRITERIA, LABELS, expected_ar, expected_od

SKILL_ICONS = {
    "aim": ":material/my_location:",
    "speed": ":material/speed:",
    "stamina": ":material/battery_charging_full:",
    "accuracy": ":material/adjust:",
    "reading": ":material/visibility:",
    "tech": ":material/gesture:",
    "exam": ":material/school:",
}

SKILL_PLAIN = {
    "aim": "viser juste sur les sauts entre les cercles",
    "speed": "taper vite et régulièrement (bursts, streams)",
    "stamina": "tenir la distance sur des maps longues ou des longs streams",
    "accuracy": "taper pile en rythme pour faire un maximum de 300",
    "reading": "lire les cercles qui arrivent très tôt ou très tard",
    "tech": "suivre les sliders et les rythmes inhabituels",
}

# rôle -> (icône, couleur du badge)
ROLES = {
    "Échauffement": (":material/local_fire_department:", "orange"),
    "Test de niveau": (":material/straighten:", "violet"),
    "Objectif": (":material/flag:", "primary"),
    "Entraînement": (":material/fitness_center:", "primary"),
    "Examen": (":material/school:", "red"),
    "Défi": (":material/bolt:", "yellow"),
    "Révision": (":material/replay:", "blue"),
}
THEME_CSS = Path(__file__).with_name("theme.css")
OLD_ROLE_NAMES = {"Placement": "Test de niveau", "Palier": "Objectif", "Entretien": "Révision"}


def skill_label(cat: str) -> str:
    return "Examen" if cat == "exam" else LABELS[cat]


def length_text(seconds: float) -> str:
    minutes, sec = divmod(int(seconds), 60)
    return f"{minutes}:{sec:02d}"


def criterion_text(crit, n_objects: int | None = None) -> str:
    miss = f"{num(crit.max_miss_pct)} % de miss maximum"
    if n_objects:
        miss += f" (environ {crit.max_miss(n_objects)} sur cette map)"
    return f"au moins {crit.min_acc:g} % de précision et {miss}"


def map_info(conn, beatmap_id: int) -> dict:
    row = conn.execute(
        """SELECT b.artist, b.title, b.version, b.beatmapset_id, a.stars, a.attrs
           FROM beatmaps b LEFT JOIN map_attrs a ON a.beatmap_id = b.beatmap_id AND a.mods_key = ''
           WHERE b.beatmap_id = ?""",
        (beatmap_id,),
    ).fetchone()
    if not row:
        return {"title": f"Map {beatmap_id}", "artist": "", "version": "", "set_id": None, "stars": None,
                "attrs": {}, "url": f"https://osu.ppy.sh/beatmaps/{beatmap_id}"}
    return {
        "title": row["title"], "artist": row["artist"], "version": row["version"], "set_id": row["beatmapset_id"],
        "stars": row["stars"], "attrs": json.loads(row["attrs"]) if row["attrs"] else {},
        "url": f"https://osu.ppy.sh/beatmaps/{beatmap_id}",
    }


def game_link(beatmap_id: int) -> str:
    """Ouvre la map directement dans osu! lazer (téléchargement compris, gratuit dans lazer)."""
    return f"osu://b/{beatmap_id}"


def _ignore(beatmap_id: int, day: str) -> None:
    """Rappel de bouton : il s'exécute au début du passage suivant, donc avec sa propre connexion."""
    conn = db.connect()
    planner.ignore_map(conn, beatmap_id, day)
    conn.close()


def why_map(cat: str, a: dict) -> str:
    """En une phrase, ce que la map a de particulier pour cette compétence."""
    if not a:
        return ""
    s = a["stars"]
    if cat == "aim":
        return "Beaucoup de sauts : elle demande plus de visée que les autres maps de ce niveau."
    if cat == "speed":
        if a.get("max_run", 0) >= 5:
            return f"Des bursts et streams jusqu'à {a['max_run']} notes d'affilée, à {a['stream_bpm']:.0f} BPM."
        return f"Un rythme rapide pour son niveau ({a['bpm']:.0f} BPM)."
    if cat == "stamina":
        if a.get("max_run", 0) >= 12:
            return f"De longs streams ({a['max_run']} notes d'affilée) sur {length_text(a['length_s'])}."
        return f"Une map longue ({length_text(a['length_s'])}) : il faut tenir jusqu'au bout."
    if cat == "accuracy":
        od, usual = f"{a['od']:g}".replace(".", ","), f"{expected_od(s):.1f}".replace(".", ",")
        return f"OD {od}, plus sévère que d'habitude à ce niveau (OD {usual} en moyenne) : il faut taper pile en rythme."
    if cat == "reading":
        if a.get("hidden"):
            return "Avec Hidden : les cercles disparaissent avant que tu cliques."
        if a["ar"] < expected_ar(s):
            return f"AR {num(a['ar'])}, lent pour ce niveau : les cercles apparaissent tôt, il faut bien lire l'ordre."
        return f"AR {num(a['ar'])}, rapide pour ce niveau : les cercles arrivent vite, il faut réagir tôt."
    if cat == "tech":
        return "Des sliders difficiles, qui comptent plus que d'habitude dans la difficulté."
    return "Une map polyvalente qui teste un peu toutes les compétences."


def role_sentence(role: str, cat: str, level_c: int, conn=None) -> str:
    label = skill_label(cat)
    if role == "Échauffement":
        return f"Une map facile dans ton point fort ({label}) pour te chauffer les doigts."
    if role == "Test de niveau":
        return f"Pas encore assez de parties en {label} : joue-la normalement, elle sert à mesurer ton niveau."
    if role == "Objectif":
        return f"Une des 3 maps à réussir pour valider ton palier {label} {stars(level_c / 100)}."
    if role == "Entraînement":
        return f"Du travail ciblé en {label}."
    if role == "Examen":
        return f"Réussis les maps de l'examen pour passer au niveau {stars(level_c / 100)}."
    if role == "Défi":
        return f"Plus dure que ton niveau en {label} : si tu la rates, c'est normal, elle t'entraîne."
    return f"Pour garder ton niveau en {label}."


def goal_sentence(role: str, crit, n_objects: int | None = None) -> str:
    if role in ("Objectif", "Examen"):
        return f"Objectif : {criterion_text(crit, n_objects)}."
    if role == "Test de niveau":
        return "Joue-la sans pression : le résultat sert seulement à te mesurer."
    if role == "Échauffement":
        return "Pas d'objectif : c'est pour te mettre en route."
    if role == "Défi":
        return f"La finir est déjà une réussite. Si tu y arrives, vise {criterion_text(crit, n_objects)}."
    return f"Vise {criterion_text(crit, n_objects)}."


def record_text(best: dict) -> str:
    """Meilleur essai en clair ; précise quand il s'agit d'un fail (la précision s'arrête au moment du fail)."""
    score = f"{pct(best['accuracy'])} · {best['misses']} miss"
    return f"ton record : {score}" if best["passed"] else f"pas encore finie · meilleur essai : fail à {score}"


def item_state(conn, role: str, cat: str, beatmap_id: int, day: str) -> tuple[str, str]:
    """('done' | 'retry' | 'todo', statut en clair)."""
    crit = progression.criterion_for(cat)
    today = progression.played_on(conn, beatmap_id, day)
    best = progression.best_result(conn, beatmap_id, crit)
    if role in ("Objectif", "Examen"):
        if best and best["met"]:
            return "done", f"Réussie · {pct(best['accuracy'])} · {best['misses']} miss"
        if today:
            last = today[0]
            detail = "fail" if not last["passed"] else f"{pct(last['accuracy'])} · {last['n_miss']} miss"
            return "retry", f"À retenter · dernier essai : {detail}"
        if best:
            return "todo", f"À jouer · {record_text(best)}"
        return "todo", "À jouer"
    if today:
        last = today[0]
        detail = "fail" if not last["passed"] else f"{pct(last['accuracy'])} · {last['n_miss']} miss"
        return "done", f"Jouée · {detail}"
    return "todo", "À jouer"


def map_card(conn, beatmap_id: int, *, role: str | None = None, cat: str, level_c: int = 0,
             number: int | None = None, day: str | None = None, highlight: bool = False,
             reason: str | None = None, p: float | None = None, minutes: float | None = None,
             ignorable: bool = False, key: str = "") -> None:
    """Carte d'une map (bannière, rôle, étoiles, chances, statut) suivie de ses boutons.

    reason, p et minutes viennent du planificateur : pourquoi cette map, tes chances d'atteindre
    l'objectif et le temps estimé. ignorable ajoute « Ne plus me proposer » (programme du jour).
    """
    info = map_info(conn, beatmap_id)
    crit = progression.criterion_for(cat)
    role = OLD_ROLE_NAMES.get(role, role)
    lead = reason or (role_sentence(role, cat, level_c) if role else "")
    hp = info["attrs"].get("hp")
    hp_note = (f"Jauge de vie exigeante (HP {num(hp)}) : elle se finit moins facilement que ses étoiles "
               "ne le laissent penser." if hp is not None and hp - HP_REF >= 1.5 else "")
    if role and day:
        state, label = item_state(conn, role, cat, beatmap_id, day)
    else:
        res = progression.best_result(conn, beatmap_id, crit)
        state, label = (("done", "Réussie") if res and res["met"] else
                        ("todo", record_text(res)[0].upper() + record_text(res)[1:]) if res else
                        ("todo", "Pas encore jouée"))
    goal = goal_sentence(role or "Objectif", crit, info["attrs"].get("n_objects"))
    with st.container(key=f"mapcard-{key or number or 'x'}-{beatmap_id}", gap="small"):
        st.html(visuals.map_card(
            info, number=number, role=role, cat=cat, skill_label=skill_label(cat),
            state={"state": state, "label": label}, reason=" ".join(x for x in [lead, why_map(cat, info["attrs"])] if x),
            goal=goal, p=p, minutes=minutes, hero=highlight, hp_note=hp_note))
        with st.container(horizontal=True, gap="small"):
            st.link_button("Ouvrir dans osu!", game_link(beatmap_id), type="primary" if highlight else "secondary",
                           icon=":material/sports_esports:",
                           help="Ouvre la map dans osu! lazer, qui la télécharge si besoin.")
            st.link_button("Page web", info["url"], type="tertiary", icon=":material/open_in_new:")
            if ignorable and day:
                st.button("Ne plus me proposer", key=f"ignore-{beatmap_id}-{number}", type="tertiary",
                          icon=":material/block:", on_click=_ignore, args=(beatmap_id, day),
                          help="Elle sort du programme et des paliers (une autre la remplace). "
                               "Annulable dans l'onglet Progression.")


def styled_chart(chart):
    """Graphique Altair aux couleurs du thème : fond transparent, grille discrète, police du thème."""
    return (chart.configure(background="transparent", font="Outfit")
            .configure_view(strokeWidth=0)
            .configure_axis(gridColor="rgba(255,255,255,0.06)", domain=False, tickColor="rgba(255,255,255,0.1)",
                            labelColor="#a99bbd", titleColor="#a99bbd", labelFontSize=12)
            .configure_legend(labelColor="#f3eefa", labelFontSize=13, symbolSize=120, symbolStrokeWidth=4))


def inject_theme() -> None:
    """Feuille de style du thème « lazer » (fond animé, cartes, néons) : à appeler une fois par page."""
    st.html(THEME_CSS)


def since(iso: str) -> str:
    """« il y a 12 min » à partir d'une date ISO (UTC)."""
    delta = datetime.now().astimezone() - datetime.fromisoformat(iso.replace("Z", "+00:00"))
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "à l'instant"
    if minutes < 60:
        return f"il y a {minutes} min"
    if minutes < 48 * 60:
        return f"il y a {minutes // 60} h"
    return f"il y a {minutes // 1440} jours"
