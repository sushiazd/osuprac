"""But de chaque map du programme, s'il est atteint, et la correction des chances apprise sur tes séances.

Un seul endroit décide de ce qu'une map demande : l'affichage (ui), le suivi des séances et la correction des chances
(update_session_offset) jugent donc tous de la même façon.
"""
from . import db, model, progression
from .skills import CLEAN_FINISH, meets

# Maps dont l'objectif est ta précision habituelle sur la map (goal_acc), et non un seuil fixe.
PERSONAL_ROLES = ("Entraînement", "Révision", "Préparation")
# Quoi viser : "finir", "propre" (finir avec au plus 8 % de miss), "acc" (finir avec au moins goal_acc %),
# "critère" (l'objectif fixe de la compétence, comme pour les paliers).


def kind(role: str, goal: str | None = None, goal_acc: float | None = None) -> str:
    if role == "Défi":
        return "finir"
    if role == "Montée":
        return "propre"
    if role == "Projet":
        return goal or "finir"
    if role in PERSONAL_ROLES and goal_acc is not None:
        return "acc"
    return "critère"


def achieved(k: str, category: str, passed: bool, accuracy: float | None, n_miss: int, n_objects: int,
             goal_acc: float | None = None) -> bool:
    if k == "finir":
        return bool(passed)
    if k == "propre":
        return meets(bool(passed), accuracy or 0.0, n_miss, n_objects, CLEAN_FINISH)
    if k == "acc":
        return bool(passed) and (accuracy or 0.0) >= (goal_acc or 0.0) - 1e-9
    return meets(bool(passed), accuracy or 0.0, n_miss, n_objects, progression.criterion_for(category))


def first_attempts(conn, recent: int | None = None, before_day: str | None = None) -> list[dict]:
    """Premiers essais du jour sur les maps du programme : chance prévue (brute et affichée) et but atteint ou non,
    des plus récents aux plus anciens. Sans les projets (leur chance porte sur toute la séance, pas sur un essai)
    ni l'examen."""
    rows = conn.execute(
        "SELECT day, role, category, beatmap_id, p, p_raw, goal, goal_acc FROM session_plan "
        "WHERE p IS NOT NULL AND role NOT IN ('Examen', 'Projet') ORDER BY day DESC, pos").fetchall()
    out = []
    for r in rows:
        if before_day and r["day"] >= before_day:
            continue
        plays = progression.played_on(conn, r["beatmap_id"], r["day"])
        if not plays:
            continue
        first = plays[-1]  # played_on : du plus récent au plus ancien
        k = kind(r["role"], r["goal"], r["goal_acc"])
        out.append({"day": r["day"], "role": r["role"], "p": r["p"], "p_raw": r["p_raw"] if r["p_raw"] is not None else r["p"],
                    "met": achieved(k, r["category"], first["passed"], first["accuracy"], first["n_miss"],
                                    progression.n_objects_of(conn, r["beatmap_id"]), r["goal_acc"])})
        if recent and len(out) >= recent:
            break
    return out


def update_session_offset(conn) -> dict:
    """Recale les chances de séance sur tes derniers premiers essais du programme (toutes maps confondues).

    La correction s'apprend sur la chance BRUTE (avant correction) : pas de boucle où elle se corrigerait elle-même.
    """
    samples = [(model.logit(a["p_raw"]), 1.0 if a["met"] else 0.0, 1.0)
               for a in first_attempts(conn, model.SESSION_OFFSET_RECENT)]
    out = {"d": round(model.fit_offset(samples), 3) if samples else 0.0, "n": len(samples)}
    db.set_meta(conn, "session_offset", out)
    return out


def track_record(conn, recent: int = 30) -> dict:
    """Tes derniers premiers essais du programme : chances affichées en moyenne et réussite réelle."""
    att = first_attempts(conn, recent)
    if not att:
        return {"n": 0}
    return {"n": len(att), "shown": sum(a["p"] for a in att) / len(att), "met": sum(a["met"] for a in att) / len(att)}
