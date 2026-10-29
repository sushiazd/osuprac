"""Note par compétence façon Elo : chaque partie est un match entre toi et la map.

Ta note dans une compétence = le niveau d'étoiles où tu atteins le critère
de cette compétence environ une fois sur deux.
"""
import json
import math
import statistics
from collections import defaultdict

from . import db
from .mods import EASY_MODS
from .skills import CATEGORIES, CLEAN_FINISH, COMMON_CRITERION, CRITERIA, meets, outcome

# Raideur de la courbe « étoiles → chances de réussir » : 0,5★ au-dessus de ta note ≈ 27 % de chances.
# Calibré en rejouant 346 parties réelles (prédiction faite avant chaque résultat) : 0,30 rendait le
# modèle trop sûr de lui (log-loss 0,542), 0,50 est le meilleur compromis (0,506). Une partie
# inhabituelle est donc moins punie, et une belle performance sur une map dure mieux récompensée.
SCALE = 0.50
K_MAX = 0.60        # grosse correction tant qu'on a peu de parties
K_MIN = 0.08        # correction minimale une fois la note stabilisée
K_HALF_GAMES = 8    # nombre de parties pour diviser la correction par deux
DEFAULT_OBJECTS = 400  # si le nombre de notes d'une map est inconnu
HP_REF = 4.0           # HP habituel des maps jouées : sert de référence
# Une jauge de vie plus dure rend une map plus difficile que ses étoiles ne le disent. Mesuré sur de vraies
# parties : ≈ 0,15★ par point de HP pour l'objectif de précision (calibré par rejeu), bien plus pour simplement
# finir la map (voir fit_clear_level).
HP_K_RATING = 0.15
SEED_WEIGHT = 0.5   # les scores d'avant le suivi (top plays) comptent moitié


def effective_stars(stars: float, hp: float | None, k: float = HP_K_RATING) -> float:
    """Étoiles corrigées de la jauge de vie : difficulté réelle d'une map pour l'objectif de précision."""
    return stars + k * ((hp if hp is not None else HP_REF) - HP_REF)


def expected(rating: float, stars: float) -> float:
    return 1 / (1 + math.exp(-(rating - stars) / SCALE))


def seed_rating(rows) -> float:
    """Niveau de départ, volontairement prudent : médiane de tes 30 meilleures parties - 0,5★.

    Tes top plays sont des réussites sur tes maps favorites : les compétences bien
    représentées remontent vite, celles qui n'y figurent pas restent basses jusqu'à
    ce que tu les joues (c'est le rôle des maps de placement).
    """
    passed = sorted((r for r in rows if r["passed"]), key=lambda r: r["pp"] or 0, reverse=True)
    top = [r["stars"] for r in passed[:30]]
    if not top:
        return 3.0
    return round(statistics.median(top) - 0.5, 2)


def recompute(conn) -> dict[str, dict]:
    """Rejoue tout l'historique dans l'ordre chronologique et enregistre les notes."""
    rows = conn.execute(
        """SELECT s.score_id, s.beatmap_id, s.accuracy, s.n_miss, s.passed, s.pp, s.ended_at,
                  a.stars, a.weights, a.attrs
           FROM scores s JOIN map_attrs a
             ON a.beatmap_id = s.beatmap_id AND a.mods_key = s.mods_key
           WHERE s.countable = 1 AND NOT s.abandoned
           ORDER BY s.ended_at"""
    ).fetchall()
    tracking_start = db.get_meta(conn, "tracking_start", "")
    start = seed_rating(rows)
    ratings = {c: start for c in CATEGORIES}
    # Même calcul avec un critère identique pour toutes les compétences : sert à les COMPARER. Les objectifs n'ont pas
    # tous la même exigence (85 % de précision en Accuracy, 80 % ailleurs) : comparées telles quelles, la compétence
    # la plus exigeante paraît toujours la plus faible.
    levels = {c: start for c in CATEGORIES}
    games = {c: 0.0 for c in CATEGORIES}
    attempts = defaultdict(int)
    events = []

    for r in rows:
        weights = json.loads(r["weights"])
        attrs = json.loads(r["attrs"])
        n_objects = attrs.get("n_objects") or DEFAULT_OBJECTS
        stars = effective_stars(r["stars"], attrs.get("hp"))
        era = SEED_WEIGHT if r["ended_at"] < tracking_start else 1.0
        day_key = (r["beatmap_id"], r["ended_at"][:10])
        attempts[day_key] += 1
        repeat = 1 / math.sqrt(attempts[day_key])  # rejouer la même map le même jour compte moins
        for cat, w in weights.items():
            p = expected(ratings[cat], stars)
            o = outcome(bool(r["passed"]), r["accuracy"], r["n_miss"], n_objects, CRITERIA[cat])
            k = K_MIN + (K_MAX - K_MIN) / (1 + games[cat] / K_HALF_GAMES)
            delta = k * w * era * repeat * (o - p)
            ratings[cat] += delta
            o_common = outcome(bool(r["passed"]), r["accuracy"], r["n_miss"], n_objects, COMMON_CRITERION)
            levels[cat] += k * w * era * repeat * (o_common - expected(levels[cat], stars))
            games[cat] += w * era * repeat
            events.append((r["score_id"], cat, w, round(p, 3), o, round(delta, 4), round(ratings[cat], 3)))

    conn.execute("DELETE FROM rating_events")
    conn.executemany("INSERT INTO rating_events VALUES (?, ?, ?, ?, ?, ?, ?)", events)
    conn.execute("DELETE FROM ratings")
    conn.executemany(
        "INSERT INTO ratings (category, rating, games, level) VALUES (?, ?, ?, ?)",
        [(c, round(ratings[c], 3), round(games[c], 2), round(levels[c], 3)) for c in CATEGORIES],
    )
    conn.commit()
    return load(conn)


def load(conn) -> dict[str, dict]:
    rows = conn.execute("SELECT category, rating, games, level FROM ratings").fetchall()
    return {r["category"]: {"rating": r["rating"], "games": r["games"],
                            "level": r["level"] if r["level"] is not None else r["rating"]} for r in rows}


def fit_clear_level(points: list[tuple]) -> dict | None:
    """Niveau d'étoiles où tu finis une map une fois sur deux, par régression logistique.

    points = [(étoiles, finie 0/1)] ou [(étoiles, HP, finie 0/1)]. Avec le HP, on estime aussi combien
    d'étoiles « vaut » un point de HP pour survivre (hp_k). Indépendant des objectifs de précision.
    """
    pts = [(p[0], (p[1] if len(p) == 3 else HP_REF) - HP_REF, p[-1]) for p in points]
    if len(pts) < 20 or not any(y for _, _, y in pts) or all(y for _, _, y in pts):
        return None

    def sigmoid(z: float) -> float:
        return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))

    # Newton-Raphson amorti sur p = sigmoïde(a + b·étoiles + c·ΔHP). Léger lissage sur a et b ; plus fort sur c
    # (sans données, le HP n'a pas d'effet).
    prior = (1e-2, 1e-2, 6.0)
    w = [3.5 * 3, -3.0, 0.0]
    for _ in range(100):
        g = [0.0] * 3
        h = [[0.0] * 3 for _ in range(3)]
        for x, dh, y in pts:
            f = (1.0, x, dh)
            p = sigmoid(sum(wi * fi for wi, fi in zip(w, f)))
            v = p * (1 - p) + 1e-6
            for i in range(3):
                g[i] += (y - p) * f[i]
                for j in range(3):
                    h[i][j] += v * f[i] * f[j]
        g[2] -= prior[2] * w[2]
        for i in range(3):
            h[i][i] += prior[i]
        m = [h[i][:] + [g[i]] for i in range(3)]
        singular = False
        for i in range(3):
            piv = max(range(i, 3), key=lambda r: abs(m[r][i]))
            if abs(m[piv][i]) < 1e-12:
                singular = True
                break
            m[i], m[piv] = m[piv], m[i]
            m[i] = [vv / m[i][i] for vv in m[i]]
            for r in range(3):
                if r != i:
                    m[r] = [a_ - m[r][i] * b_ for a_, b_ in zip(m[r], m[i])]
        if singular:
            return None
        step_v = [m[i][3] for i in range(3)]
        step = max(abs(v) for v in step_v)
        if step > 1.0:
            step_v = [v / step for v in step_v]
        w = [wi + si for wi, si in zip(w, step_v)]
        if step < 1e-8:
            break
    a, b, c = w
    if b >= 0:
        return None
    level = -a / b
    highest = max(x for x, _, y in pts if y)
    return {"level": round(max(0.5, min(level, 9.0)), 2), "highest": round(highest, 2), "plays": len(pts),
            "scale": round(max(0.2, min(-1 / b, 1.5)), 2), "hp_k": round(max(0.0, min(c / b, 0.6)), 3)}


def clear_level(conn, clean: bool = False) -> dict | None:
    """Niveau de réussite (sans fail) d'après tes parties récentes, sans mod facilitant.

    clean=True : niveau où tu finis une map PROPREMENT (au plus 8 % de miss, voir skills.CLEAN_FINISH) une fois sur
    deux. Sert aux maps « Montée ».
    """
    rows = conn.execute(
        """SELECT s.passed, s.mods, s.accuracy, s.n_miss, a.stars, a.attrs FROM scores s
           JOIN map_attrs a ON a.beatmap_id = s.beatmap_id AND a.mods_key = s.mods_key
           WHERE s.countable = 1 AND NOT s.abandoned AND s.source IN ('recent', 'lazer_local')"""
    ).fetchall()
    points = []
    for r in rows:
        if {m["acronym"] for m in json.loads(r["mods"])} & EASY_MODS:
            continue
        attrs = json.loads(r["attrs"])
        ok = (meets(bool(r["passed"]), r["accuracy"], r["n_miss"], attrs.get("n_objects") or DEFAULT_OBJECTS,
                    CLEAN_FINISH) if clean else bool(r["passed"]))
        points.append((r["stars"], attrs.get("hp", HP_REF), 1.0 if ok else 0.0))
    return fit_clear_level(points)
