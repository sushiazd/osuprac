"""Projets : les maps que tu t'acharnes à passer (vu le 2026-11-11 : une 5,2★ jouée 21 fois en 10 jours, une 4,1★
19 fois), repérées toutes seules. Pour chacune : un but par étape (la finir, puis la finir proprement), tes progrès
jour après jour (jusqu'où tu vas quand tu fails, ta précision quand tu la finis), et dans la séance du jour quelques
maps du même style pour la préparer, puis un essai.
"""
import json
from datetime import date, timedelta

from . import db, model, progression
from .mods import EASY_MODS
from .rating import HP_REF
from .skills import CLEAN_FINISH, meets

MIN_ATTEMPTS = 5        # parties sur la map…
MIN_DAYS = 2            # … sur au moins 2 jours différents : un vrai acharnement, pas une soirée de relances
ACTIVE_DAYS = 14        # jouée dans les 14 derniers jours
WINDOW_DAYS = 30        # l'acharnement se mesure sur les 30 derniers jours
MAX_PROJECTS = 3
DONE_SHOWN_DAYS = 3     # un projet réussi reste affiché (« réussi ! ») quelques jours
PRIOR_PLAYS = 3.0       # poids du modèle face à tes essais réels sur la map (jours), pour ses chances


def _plays(conn, since: str) -> list:
    return conn.execute(
        """SELECT s.beatmap_id, s.passed, s.accuracy, s.n_miss, s.mods, s.ended_at,
                  s.n_great + s.n_ok + s.n_meh + s.n_miss AS judged
           FROM scores s WHERE s.countable = 1 AND NOT s.abandoned AND s.source IN ('recent', 'lazer_local')
             AND s.ended_at >= ? ORDER BY s.ended_at""", (since,)).fetchall()


def dismissed(conn) -> set[int]:
    return set(db.get_meta(conn, "projects_dismissed", []))


def dismiss(conn, beatmap_id: int) -> None:
    db.set_meta(conn, "projects_dismissed", sorted(dismissed(conn) | {beatmap_id}))
    conn.commit()


def days_of(conn, beatmap_id: int, n_objects: int) -> list[dict]:
    """Une ligne par jour joué : essais, plus loin atteint (part de la map, 1 = finie), finie, propre, meilleure
    précision et moins de miss quand finie. Du plus ancien au plus récent."""
    rows = conn.execute(
        """SELECT passed, accuracy, n_miss, mods, ended_at, n_great + n_ok + n_meh + n_miss AS judged FROM scores
           WHERE beatmap_id = ? AND countable = 1 AND NOT abandoned AND source != 'inferred_fail' ORDER BY ended_at""",
        (beatmap_id,)).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        if {m["acronym"] for m in json.loads(r["mods"])} & EASY_MODS:
            continue
        d = out.setdefault(db.local_day(r["ended_at"]), {"attempts": 0, "reach": 0.0, "passed": False, "clean": False,
                                                          "best_acc": None, "min_miss": None})
        d["attempts"] += 1
        reach = 1.0 if r["passed"] else min(0.99, (r["judged"] or 0) / max(1, n_objects))
        d["reach"] = max(d["reach"], reach)
        if r["passed"]:
            d["passed"] = True
            d["clean"] = d["clean"] or meets(True, r["accuracy"], r["n_miss"], n_objects, CLEAN_FINISH)
            d["best_acc"] = max(d["best_acc"] or 0.0, r["accuracy"])
            d["min_miss"] = r["n_miss"] if d["min_miss"] is None else min(d["min_miss"], r["n_miss"])
    return [{"day": k, **v} for k, v in sorted(out.items())]


def stage(days: list[dict]) -> str:
    """Étape du projet : « finir » tant que jamais finie, puis « propre », puis « réussi »."""
    if any(d["clean"] for d in days):
        return "réussi"
    if any(d["passed"] for d in days):
        return "propre"
    return "finir"


def active(conn, today: str | None = None) -> list[dict]:
    """Projets en cours (et ceux réussis ces derniers jours), du plus récemment joué au plus ancien."""
    today_d = date.fromisoformat(today) if today else date.today()
    since = (today_d - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%dT00:00:00Z")
    per_map: dict[int, dict] = {}
    for r in _plays(conn, since):
        if {m["acronym"] for m in json.loads(r["mods"])} & EASY_MODS:
            continue
        m = per_map.setdefault(r["beatmap_id"], {"n": 0, "days": set(), "last": ""})
        m["n"] += 1
        m["days"].add(db.local_day(r["ended_at"]))
        m["last"] = max(m["last"], db.local_day(r["ended_at"]))
    skip = dismissed(conn) | progression.ignored_maps(conn)
    out = []
    for bid, m in per_map.items():
        if bid in skip or m["n"] < MIN_ATTEMPTS or len(m["days"]) < MIN_DAYS:
            continue
        if (today_d - date.fromisoformat(m["last"])).days > ACTIVE_DAYS:
            continue
        n_obj = progression.n_objects_of(conn, bid)
        days = days_of(conn, bid, n_obj)
        st = stage(days)
        if st == "réussi":
            first_clean = next(d["day"] for d in days if d["clean"])
            # Réussi avant même d'être un acharnement (finie proprement dès le début) : pas un projet.
            if sum(1 for d in days if d["day"] < first_clean) < MIN_DAYS - 1 or \
                    (today_d - date.fromisoformat(first_clean)).days > DONE_SHOWN_DAYS:
                continue
        out.append({"beatmap_id": bid, "stage": st, "attempts": m["n"], "days": days, "last": m["last"],
                    "n_objects": n_obj})
    out.sort(key=lambda p: p["last"], reverse=True)
    return out[:MAX_PROJECTS]


def chance(conn, project: dict, clear: dict | None, clean: dict | None, offset: float = 0.0) -> float | None:
    """Chances d'atteindre le but du projet au prochain essai : le modèle (étoiles, HP), corrigé par tes vrais
    résultats sur cette map (jours où tu l'as atteint / jours joués, les 7 derniers)."""
    row = conn.execute("SELECT stars, attrs FROM map_attrs WHERE beatmap_id = ? AND mods_key = ''",
                       (project["beatmap_id"],)).fetchone()
    if not row:
        return None
    hp = json.loads(row["attrs"]).get("hp", HP_REF)
    level = clean if project["stage"] == "propre" else clear
    p_model = model.corrected(model.finish_prob_for(level, row["stars"], hp), offset)
    recent = project["days"][-7:]
    key = "clean" if project["stage"] == "propre" else "passed"
    hits = sum(1 for d in recent if d[key])
    prior = p_model if p_model is not None else 0.3
    return (PRIOR_PLAYS * prior + hits) / (PRIOR_PLAYS + len(recent))
