"""Modèle de prévision partagé : chances de réussir une map, incertitude des notes, qualité d'une map.

Fonctions pures (sans accès à la base), utilisées par le planificateur d'entraînement et par le
choix des maps de palier.
"""
import math

from .rating import HP_REF, SCALE

LOGIT_CLIP = 30.0
# Correction apprise sur les maps du programme : sur tes 51 premiers essais vérifiables (octobre et 1er novembre), les
# chances affichées étaient de 54 % pour 69 % de réussite, alors que le recalage est juste sur l'ensemble de tes
# parties. On décale donc les chances de ce que montrent tes séances, prudemment (a priori resserré vers 0).
PLAN_ROLES = ("Échauffement", "Entraînement", "Objectif", "Révision", "Test de niveau")  # chances = objectif atteint
PLAN_OFFSET_RECENT = 80     # derniers premiers essais pris en compte : la correction suit ta progression
PLAN_OFFSET_PRIOR_SD = 0.5  # en logit : avec ~50 essais, l'écart mesuré est retenu aux trois quarts environ
PLAN_OFFSET_MAX = 1.0
CALIBRATED_GAMES = 3      # en dessous, la note d'une compétence est encore trop incertaine
DEFAULT_CLEAR_SCALE = 0.55


def sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-max(-LOGIT_CLIP, min(LOGIT_CLIP, z))))


def logit(p: float) -> float:
    p = min(0.998, max(0.002, p))
    return math.log(p / (1 - p))


def skill_sd(games: float) -> float:
    """Incertitude (en ★) d'une note de compétence.

    Mesurée par rééchantillonnage de vraies parties : environ ±0,12★ avec beaucoup de parties,
    ±0,25★ vers 8 parties, ±0,45★ sans aucune partie.
    """
    return math.sqrt(0.11 ** 2 + 0.45 ** 2 / (1 + games / 3))


def p_skill(rating: float, stars: float, calib: tuple[float, float] = (0.0, 1.0)) -> float:
    """Chances d'atteindre l'objectif d'une compétence sur une map de `stars`, avec calibration."""
    a, b = calib
    return sigmoid(a + b * (rating - stars) / SCALE)


def p_map(weights: dict[str, float], ratings: dict[str, float], stars: float,
          calib: tuple[float, float] = (0.0, 1.0)) -> float:
    """Chances d'atteindre l'objectif sur une map : moyenne des compétences, pondérée par la map."""
    total = sum(weights.values()) or 1.0
    return sum(w * p_skill(ratings[s], stars, calib) for s, w in weights.items()) / total


def finish_prob(stars: float, hp: float | None, level: float, scale: float, hp_k: float = 0.0) -> float:
    """Chances de finir une map : ton niveau de réussite, corrigé de la jauge de vie (HP) de la map."""
    h = hp if hp is not None else HP_REF
    return sigmoid((level - (stars + hp_k * (h - HP_REF))) / scale)


def finish_prob_for(clear: dict | None, stars: float, hp: float | None) -> float | None:
    """finish_prob avec ton niveau de réussite enregistré (None tant qu'il n'est pas mesuré)."""
    if not clear:
        return None
    return finish_prob(stars, hp, clear["level"], clear.get("scale", DEFAULT_CLEAR_SCALE), clear.get("hp_k", 0.0))


def strongest_skill(ratings: dict[str, float], games: dict[str, float]) -> str:
    """Compétence la plus haute parmi celles assez mesurées."""
    return max(ratings, key=lambda s: ratings[s] if games[s] >= CALIBRATED_GAMES else -9)


SIGNIFICANT_Z = 1.5   # écart / incertitude au-delà duquel une différence entre compétences est jugée réelle


def skill_gaps(ratings: dict[str, float], games: dict[str, float]) -> dict[str, dict]:
    """Écart de chaque compétence avec la moyenne des autres, son incertitude, et s'il dépasse la marge d'erreur.

    Avec quelques dizaines de parties, deux notes à 0,15★ d'écart sont souvent indiscernables : sans ce garde-fou,
    on présenterait du bruit comme un point faible.
    """
    out = {}
    for s in ratings:
        others = [o for o in ratings if o != s]
        mean_others = sum(ratings[o] for o in others) / len(others)
        sd_others = math.sqrt(sum(skill_sd(games[o]) ** 2 for o in others)) / len(others)
        sd = math.sqrt(skill_sd(games[s]) ** 2 + sd_others ** 2)
        gap = ratings[s] - mean_others
        out[s] = {"gap": gap, "sd": sd, "skill_sd": skill_sd(games[s]), "significant": abs(gap) > SIGNIFICANT_Z * sd}
    return out


def bump(x: float, center: float, width: float) -> float:
    """Courbe en cloche : 1 au centre, décroît avec l'écart. Sert à viser une probabilité de réussite."""
    return math.exp(-0.5 * ((x - center) / width) ** 2)


def quality(playcount: int | None, favourites: int | None) -> float:
    """Entre 0,85 et 1 : les maps populaires et aimées sont en général mieux construites."""
    pc = min(1.0, math.log10((playcount or 0) + 1) / 5.0)
    fav = min(1.0, math.log10((favourites or 0) + 1) / 3.5)
    return 0.85 + 0.15 * (0.5 * pc + 0.5 * fav)


def duration_factor(length_s: float, stamina_weight: float = 0.0) -> float:
    """Pénalise les maps très courtes (peu d'entraînement) ou très longues (fatigue, peu d'essais)."""
    longest = 420.0 if stamina_weight >= 0.4 else 240.0
    if length_s < 45:
        return 0.5
    if length_s < 95:
        return 0.5 + 0.5 * (length_s - 45) / 50   # trop court pour que la précision soit significative
    if length_s <= longest:
        return 1.0
    if length_s <= longest + 150:
        return 1.0 - 0.45 * (length_s - longest) / 150
    return 0.45


def minutes_for(length_s: float, attempts: float) -> float:
    """Durée estimée sur ta session : le temps de la map × le nombre d'essais, plus 25 s de navigation."""
    return length_s / 60 * attempts + 0.4


def fit_calibration(samples: list[tuple[float, float, float]], prior: float = 6.0) -> tuple[float, float]:
    """Régression logistique de (z, résultat, poids) → (a, b) avec P = sigmoïde(a + b·z).

    Corrige les biais du modèle (par exemple une note qui court derrière un joueur qui progresse vite).
    Un a priori vers (0, 1) évite les valeurs extrêmes quand il y a peu de données.
    """
    a, b = 0.0, 1.0
    for _ in range(50):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for z, o, w in samples:
            p = sigmoid(a + b * z)
            v = w * p * (1 - p)
            g0 += w * (o - p)
            g1 += w * (o - p) * z
            h00 += v
            h01 += v * z
            h11 += v * z * z
        g0 -= prior * a
        g1 -= prior * (b - 1)
        h00 += prior
        h11 += prior
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-9:
            break
        da, db = (h11 * g0 - h01 * g1) / det, (h00 * g1 - h01 * g0) / det
        step = max(abs(da), abs(db))
        if step > 0.5:
            da, db = da * 0.5 / step, db * 0.5 / step
        a, b = a + da, b + db
        if step < 1e-6:
            break
    return max(-1.0, min(1.5, a)), max(0.6, min(1.6, b))


def fit_offset(samples: list[tuple[float, float, float]], prior_sd: float = PLAN_OFFSET_PRIOR_SD) -> float:
    """Décalage d (en logit) qui ajuste au mieux P = sigmoïde(z + d) à des (z, résultat, poids), resserré vers 0."""
    d = 0.0
    for _ in range(50):
        g, h = -d / prior_sd ** 2, 1 / prior_sd ** 2
        for z, o, w in samples:
            p = sigmoid(z + d)
            g += w * (o - p)
            h += w * p * (1 - p)
        step = g / h
        d += step
        if abs(step) < 1e-6:
            break
    return max(-PLAN_OFFSET_MAX, min(PLAN_OFFSET_MAX, d))


def load_calibration(conn) -> tuple[float, float]:
    """(a, b) des chances affichées et utilisées pour choisir les maps, correction des séances comprise."""
    from . import db  # import tardif : db importe rating
    cal = db.get_meta(conn, "calibration")
    return (cal["a"] + cal.get("plan_offset", 0.0), cal["b"]) if cal else (0.0, 1.0)


def planned_first_attempts(conn, recent: int = PLAN_OFFSET_RECENT) -> list[int]:
    """Parties qui sont le premier essai du jour d'une map du programme (rôles dont les chances visent l'objectif),
    des plus récentes aux plus anciennes."""
    from . import db
    plans = conn.execute(
        f"SELECT DISTINCT day, beatmap_id FROM session_plan WHERE role IN ({','.join('?' * len(PLAN_ROLES))})",
        PLAN_ROLES).fetchall()
    if not plans:
        return []
    wanted = {(r["day"], r["beatmap_id"]) for r in plans}
    first = {}
    for r in conn.execute(
        f"""SELECT score_id, beatmap_id, ended_at FROM scores
            WHERE countable = 1 AND NOT abandoned AND source != 'inferred_fail'
              AND beatmap_id IN ({','.join('?' * len({b for _, b in wanted}))}) ORDER BY ended_at""",
            sorted({b for _, b in wanted})):
        key = (db.local_day(r["ended_at"]), r["beatmap_id"])
        if key in wanted and key not in first:
            first[key] = (r["ended_at"], r["score_id"])
    return [sid for _, sid in sorted(first.values(), reverse=True)][:recent]


def update_calibration(conn, recent: int = 300) -> dict:
    """Recale les probabilités sur tes ~300 dernières prédictions (cela suit ta progression)."""
    from . import db
    rows = conn.execute(
        """SELECT e.expected AS p, e.outcome AS o, e.weight AS w FROM rating_events e
           JOIN scores s ON s.score_id = e.score_id
           WHERE s.source != 'inferred_fail' ORDER BY s.ended_at DESC LIMIT ?""",
        (recent,),
    ).fetchall()
    if len(rows) < 40:
        cal = {"a": 0.0, "b": 1.0, "n": len(rows)}
    else:
        # Résultat binaire : « objectif atteint » (outcome = 1) ou non. C'est ce que le planificateur prédit.
        a, b = fit_calibration([(logit(r["p"]), 1.0 if r["o"] >= 0.999 else 0.0, r["w"]) for r in rows])
        cal = {"a": round(a, 3), "b": round(b, 3), "n": len(rows)}
    planned = planned_first_attempts(conn)
    if planned:
        events = conn.execute(
            f"SELECT expected AS p, outcome AS o, weight AS w FROM rating_events WHERE score_id IN "
            f"({','.join('?' * len(planned))})", planned).fetchall()
        offset = fit_offset([(cal["a"] + cal["b"] * logit(r["p"]), 1.0 if r["o"] >= 0.999 else 0.0, r["w"])
                             for r in events])
        cal |= {"plan_offset": round(offset, 3), "plan_n": len(planned)}
    db.set_meta(conn, "calibration", cal)
    return cal
