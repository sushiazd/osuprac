"""Éléments d'interface partagés par les pages du tableau de bord (textes courts, cartes et lignes de map)."""
import json
from datetime import datetime
from pathlib import Path

import streamlit as st

from . import db, planner, pp, progression, visuals
from .fmt import num, pct, stars  # noqa: F401  (réexportés pour les pages)
from .skills import CLEAN_FINISH, LABELS

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

THEME_CSS = Path(__file__).with_name("theme.css")
OLD_ROLE_NAMES = {"Placement": "Test de niveau", "Palier": "Objectif", "Entretien": "Révision"}


def skill_label(cat: str) -> str:
    return "Examen" if cat == "exam" else LABELS[cat]


def criterion_text(crit, n_objects: int | None = None) -> str:
    miss = f"{num(crit.max_miss_pct)} % de miss maximum"
    if n_objects:
        miss += f" (environ {crit.max_miss(n_objects)} sur cette map)"
    return f"au moins {crit.min_acc:g} % de précision et {miss}"


def short_goal(crit, n_objects: int | None) -> str:
    """« 80 % de précision, 9 miss maximum » : l'objectif en une ligne."""
    misses = f"{crit.max_miss(n_objects)} miss maximum" if n_objects else f"{num(crit.max_miss_pct)} % de miss maximum"
    return f"{crit.min_acc:g} % de précision, {misses}"


def requirement_line(conn, beatmap_id: int, crit) -> str:
    """Pour une map de palier ou d'examen : ce qu'il faut atteindre, ton meilleur essai et ce qu'il te manque.

    La précision ET les miss doivent être atteints dans une même partie, finie.
    """
    n_objects = progression.n_objects_of(conn, beatmap_id)
    allowed = crit.max_miss(n_objects)
    goal = f"{num(crit.min_acc)} % de précision et {allowed} miss maximum"
    best = progression.best_result(conn, beatmap_id, crit)
    if best and best["met"]:
        return f":green[:material/check_circle: **Validée** · {pct(best['accuracy'])} · {best['misses']} miss]"
    if not best:
        return f":gray[:material/radio_button_unchecked: **À faire** · une partie finie avec {goal}.]"
    if not best["passed"]:
        missing = "finir la map"
        record = f"fail à {pct(best['accuracy'])} · {best['misses']} miss"
    else:
        gaps = []
        if best["accuracy"] < crit.min_acc:
            gaps.append(f"{num(round(crit.min_acc - best['accuracy'], 1))} point(s) de précision en plus")
        if best["misses"] > allowed:
            gaps.append(f"{best['misses'] - allowed} miss de moins")
        missing = " et ".join(gaps) or "tout atteindre dans la même partie"
        record = f"{pct(best['accuracy'])} · {best['misses']} miss"
    return (f":orange[:material/flag: **À faire** · il faut {goal}.] "
            f":gray[Ton meilleur essai : {record}. Il te manque : {missing}.]")


def map_info(conn, beatmap_id: int) -> dict:
    row = conn.execute(
        """SELECT b.artist, b.title, b.version, b.beatmapset_id, b.status, a.stars, a.attrs
           FROM beatmaps b LEFT JOIN map_attrs a ON a.beatmap_id = b.beatmap_id AND a.mods_key = ''
           WHERE b.beatmap_id = ?""",
        (beatmap_id,),
    ).fetchone()
    if not row:
        return {"title": f"Map {beatmap_id}", "artist": "", "version": "", "set_id": None, "stars": None,
                "attrs": {}, "status": None, "url": f"https://osu.ppy.sh/beatmaps/{beatmap_id}"}
    return {
        "title": row["title"], "artist": row["artist"], "version": row["version"], "set_id": row["beatmapset_id"],
        "status": row["status"],
        "stars": row["stars"], "attrs": json.loads(row["attrs"]) if row["attrs"] else {},
        "url": f"https://osu.ppy.sh/beatmaps/{beatmap_id}",
    }


def game_link(beatmap_id: int) -> str:
    """Ouvre la map directement dans osu! lazer (téléchargement compris, gratuit dans lazer)."""
    return f"osu://b/{beatmap_id}"


def _ignore(beatmap_id: int, day: str, drop_played: bool) -> None:
    """Rappel de bouton : il s'exécute au début du passage suivant, donc avec sa propre connexion."""
    conn = db.connect()
    planner.ignore_map(conn, beatmap_id, day, drop_played)
    conn.close()


def role_sentence(role: str, cat: str, level_c: int) -> str:
    """Pourquoi cette map, en une phrase courte."""
    label = skill_label(cat)
    return {
        "Échauffement": "Une map facile pour te mettre en route.",
        "Test de niveau": f"Sert à mesurer ton niveau en {label}.",
        "Objectif": f"Une des 3 maps de ton palier {label} {stars(level_c / 100)}.",
        "Entraînement": f"Travail ciblé en {label}.",
        "Examen": f"Examen du niveau {stars(level_c / 100)}.",
        "Montée": f"Près de ta limite : c'est là que ton niveau et tes pp montent ({label}).",
        "Défi": "Au-dessus de ton niveau : la rater est normal.",
        "Révision": f"Pour garder ton niveau en {label}.",
    }.get(role, "")


def goal_sentence(role: str, crit, n_objects: int | None = None) -> str:
    if role == "Échauffement":
        return "Aucun, juste te chauffer."
    if role == "Test de niveau":
        return "Joue-la normalement."
    if role == "Montée":
        limit = (f"{CLEAN_FINISH.max_miss(n_objects)} miss" if n_objects
                 else f"{num(CLEAN_FINISH.max_miss_pct)} % de miss")
        return f"La finir avec au plus {limit} (précision libre)."
    if role == "Défi":
        return f"La finir. Bonus : {short_goal(crit, n_objects)}."
    return short_goal(crit, n_objects) + "."


def record_text(best: dict) -> str:
    """Meilleur essai en clair ; précise quand il s'agit d'un fail (la précision s'arrête au moment du fail)."""
    score = f"{pct(best['accuracy'])} · {best['misses']} miss"
    return f"ton record : {score}" if best["passed"] else f"pas encore finie · meilleur essai : fail à {score}"


def item_state(conn, role: str, cat: str, beatmap_id: int, day: str) -> tuple[str, str]:
    """('done' | 'retry' | 'todo', statut en clair)."""
    crit = CLEAN_FINISH if role == "Montée" else progression.criterion_for(cat)
    today = progression.played_on(conn, beatmap_id, day)
    if role == "Montée":  # réussie seulement si finie proprement aujourd'hui ; sinon, à retenter
        n_obj = progression.n_objects_of(conn, beatmap_id)
        clean = [p for p in today if progression.meets(bool(p["passed"]), p["accuracy"], p["n_miss"], n_obj, crit)]
        if clean:
            return "done", f"Réussie · {clean[0]['n_miss']} miss"
        if today:
            last = today[0]
            detail = "fail" if not last["passed"] else f"{last['n_miss']} miss (max {crit.max_miss(n_obj)})"
            return "retry", f"À retenter · dernier essai : {detail}"
        return "todo", "À jouer"
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


def _more_menu(info: dict, beatmap_id: int, day: str | None, ignorable: bool, key: str,
               drop_played: bool = False) -> None:
    """Menu « … » : actions secondaires d'une map."""
    with st.popover("", icon=":material/more_horiz:", type="tertiary", key=f"more-{key}-{beatmap_id}"):
        st.link_button("Page de la map", info["url"], type="tertiary", icon=":material/open_in_new:")
        if ignorable and day:
            st.button("Ne plus me proposer", key=f"ignore-{key}-{beatmap_id}", type="tertiary",
                      icon=":material/block:", on_click=_ignore, args=(beatmap_id, day, drop_played),
                      help="Elle sort du programme et des paliers. Annulable dans Progression.")


def map_card(conn, beatmap_id: int, *, role: str | None = None, cat: str, level_c: int = 0,
             number: int | None = None, day: str | None = None, highlight: bool = False,
             reason: str | None = None, p: float | None = None, minutes: float | None = None,
             ignorable: bool = False, key: str = "") -> None:
    """Une map : grande carte (highlight, la map à jouer maintenant) ou ligne de liste, avec ses boutons.

    `reason` (le détail du planificateur) n'apparaît qu'en infobulle : la carte reste courte.
    """
    info = map_info(conn, beatmap_id)
    crit = progression.criterion_for(cat)
    role = OLD_ROLE_NAMES.get(role, role)
    key = f"{key}-{number}" if number is not None else key
    # Une map déjà essayée mais pas réussie (« à retenter ») sort entièrement de la liste quand on la retire.
    drop_played = bool(role and day and ignorable and item_state(conn, role, cat, beatmap_id, day)[0] == "retry")
    if highlight:
        fc = pp.fc_pp(info["attrs"], pp.typical_accuracy(conn)) if info["status"] in pp.PP_STATUSES else None
        st.html(visuals.now_card(info, role=role, skill_label=skill_label(cat),
                                 goal=goal_sentence(role or "Objectif", crit, info["attrs"].get("n_objects")),
                                 p=p, minutes=minutes, reason=role_sentence(role, cat, level_c) if role else "",
                                 fc_pp=fc))
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            st.link_button("Ouvrir dans osu!", game_link(beatmap_id), type="primary", icon=":material/play_arrow:",
                           help="Ouvre la map dans osu! lazer, qui la télécharge si besoin.")
            _more_menu(info, beatmap_id, day, ignorable, key, drop_played)
        return
    if role and day:
        state, _ = item_state(conn, role, cat, beatmap_id, day)
    else:
        res = progression.best_result(conn, beatmap_id, crit)
        state = "done" if res and res["met"] else "todo"
    tip = " ".join(x for x in [role_sentence(role, cat, level_c) if role else "",
                               "Objectif : " + goal_sentence(role or "Objectif", crit, info["attrs"].get("n_objects")),
                               reason or ""] if x)
    with st.container(horizontal=True, vertical_alignment="center", gap="small", key=f"row-{key}-{beatmap_id}"):
        st.html(visuals.map_row(info, number=number, role=role, state=state, p=p, reason=tip))
        st.link_button("", game_link(beatmap_id), type="tertiary", icon=":material/play_arrow:",
                       help="Ouvrir dans osu!")
        _more_menu(info, beatmap_id, day, ignorable, key, drop_played)


def styled_chart(chart):
    """Graphique Altair sobre : fond transparent, grille discrète."""
    return (chart.configure(background="transparent", font="Inter")
            .configure_view(strokeWidth=0)
            .configure_axis(gridColor="#202026", domain=False, tickColor="#202026",
                            labelColor="#8b8b96", titleColor="#8b8b96", labelFontSize=12, titleFontWeight=500)
            .configure_legend(labelColor="#ececf1", labelFontSize=12))


def inject_theme() -> None:
    """Styles des composants maison (theme.css) : à appeler une fois, en tête de app.py."""
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
