"""Planificateur d'entraînement : choisit les maps du jour pour te faire progresser le plus vite.

Principes (tirés de la recherche sur l'apprentissage et de la façon dont osu! se travaille) :

1. Travailler dans la zone de difficulté productive : assez de réussites pour progresser, assez
   d'erreurs pour apprendre (maps finies ~75 % du temps, objectif atteint ~50 %, réglable).
2. Investir là où le gain est le plus grand : une compétence compte en proportion de sa présence
   dans les maps que tu joueras ensuite, de son retard sur tes autres compétences et de l'incertitude
   de sa note. Les autres reçoivent moins de temps, jamais zéro.
3. Rendements décroissants : chaque map chargée sur une compétence réduit l'intérêt d'en ajouter une
   de plus ce jour-là (travail varié) et les compétences déjà très sollicitées ces 48 dernières
   heures sont ménagées (pratique espacée).
4. Rejouer les maps ratées récemment (répétition ciblée), éviter celles déjà réussies
   et celles sur lesquelles on s'acharne sans progrès.
5. Séance structurée dans un budget de temps : échauffement, travail ciblé, défi, révision.

Le cœur (`plan`) est une fonction pure : il ne touche pas à la base et peut être simulé.
"""
import hashlib
import json
import math
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import db, fmt, model, progression
from .model import CALIBRATED_GAMES, DEFAULT_CLEAR_SCALE
from .rating import HP_REF, effective_stars
from .skills import CATEGORIES, CRITERIA, LABELS

PLAN_FORMAT = 16               # à incrémenter quand l'algorithme change
# intensité -> (chances de FINIR la map visées, chances d'atteindre l'OBJECTIF de précision visées)
# « équilibré » est le réglage le plus robuste en simulation (meilleure moyenne et meilleur pire cas
# sous quatre hypothèses d'apprentissage, voir tools/simulate_training.py).
INTENSITY = {"confort": (0.85, 0.60), "équilibré": (0.76, 0.50), "intense": (0.58, 0.33)}
DEFAULT_INTENSITY = "équilibré"
DEFAULT_MINUTES = 45
TARGET_BAND = (-0.25, 0.75)   # maps que tu voudras jouer ensuite, par rapport à ton niveau de réussite
FINISH_WIDTH = 0.18
MEET_WIDTH = 0.40              # vérifié en simulation : assouplir ce critère remonte les étoiles sans coût
DIMINISH = 0.62               # chaque map chargée sur une compétence vaut 38 % de moins pour la suivante
MAX_SHARE = 0.40              # aucune compétence ne dépasse 40 % du programme (variété = meilleure rétention)
TIER_BONUS = 1.3              # une map de palier fait aussi avancer le déblocage
MAX_TESTS = 2
MAX_PER_CREATOR = 2
PLAN_FINISH_FLOOR = 0.25      # on ne propose pas de map que tu as très peu de chances de finir


# --- Structures ----------------------------------------------------------------------------

@dataclass
class Cand:
    beatmap_id: int
    set_id: int
    creator: str
    stars: float
    weights: dict
    length_s: float
    playcount: int = 0
    favourites: int = 0
    hp: float = HP_REF


@dataclass
class History:
    attempts: int = 0
    met: bool = False
    days_since: float | None = None
    best_acc: float | None = None


@dataclass
class Context:
    ratings: dict
    games: dict
    level: float                      # niveau de réussite (étoiles) autour duquel on planifie
    budget_min: float = DEFAULT_MINUTES
    target_finish: float = INTENSITY[DEFAULT_INTENSITY][0]
    target_meet: float = INTENSITY[DEFAULT_INTENSITY][1]
    seed: str = ""
    calib: tuple = (0.0, 1.0)
    clear_level: float | None = None
    clear_scale: float = DEFAULT_CLEAR_SCALE
    hp_k: float = 0.0            # étoiles de difficulté par point de HP pour finir une map
    load2d: dict = field(default_factory=dict)   # sollicitation de chaque compétence ces 48 h
    load7d: dict = field(default_factory=dict)   # … et ces 7 jours
    history: dict = field(default_factory=dict)  # beatmap_id -> History
    tier_open: dict = field(default_factory=dict)  # beatmap_id -> (compétence, niveau en centièmes)
    exam: list = field(default_factory=list)       # beatmap_id des maps d'examen à jouer
    exam_level_c: int = 0
    exclude: set = field(default_factory=set)      # maps à ne pas proposer (déjà dans la séance)
    warmed_up: bool = False                        # déjà joué aujourd'hui : pas de nouvel échauffement
    done_roles: set = field(default_factory=set)   # rôles des maps du programme déjà jouées aujourd'hui


@dataclass
class Item:
    role: str
    category: str
    beatmap_id: int
    level_c: int
    p: float
    minutes: float
    reason: str
    share: float = 0.0


# --- Briques ---------------------------------------------------------------------------------

def noise(seed: str, key) -> float:
    """Bruit déterministe dans [-1, 1] : même jour = même séance, jours différents = variété."""
    h = hashlib.blake2b(f"{seed}:{key}".encode(), digest_size=4).digest()
    return int.from_bytes(h, "big") / 2 ** 32 * 2 - 1


def freshness(h: History) -> float:
    """Intérêt de (re)jouer une map d'après ton historique avec elle."""
    if h.attempts == 0:
        return 1.0
    days = h.days_since if h.days_since is not None else 99.0
    if h.met:
        return 0.15 if days < 1 else 0.35 if days < 14 else 0.7
    if h.attempts > 8:
        return 0.5   # on s'acharne sans réussir : changer d'air
    return 1.25 if days <= 14 else 1.0  # retenter une map ratée récemment consolide les passages difficiles


def skill_shares(ctx: Context, cands: list[Cand]) -> tuple[dict, dict]:
    """Part d'entraînement de chaque compétence (somme = 1) et le détail de son calcul."""
    lo, hi = ctx.level + TARGET_BAND[0], ctx.level + TARGET_BAND[1]
    band = [c for c in cands if lo <= c.stars <= hi]
    if len(band) < 20:
        band = [c for c in cands if ctx.level - 0.75 <= c.stars <= ctx.level + 1.25] or list(cands)
    imp = {s: sum(c.weights.get(s, 0.0) for c in band) / max(1, len(band)) for s in CATEGORIES}
    total = sum(imp.values()) or 1.0
    imp = {s: max(0.02, v / total) for s, v in imp.items()}
    total = sum(imp.values())
    imp = {s: v / total for s, v in imp.items()}
    mean_rating = sum(imp[s] * ctx.ratings[s] for s in CATEGORIES)

    raw, parts = {}, {}
    for s in CATEGORIES:
        sd = model.skill_sd(ctx.games[s])
        gap = mean_rating - ctx.ratings[s]                       # > 0 : en retard sur tes autres compétences
        delta = max(-1.5, min(2.5, gap / 0.4))
        damp = 1 / (1 + ctx.load2d.get(s, 0.0) / 25)             # ménage ce qui vient d'être très sollicité
        raw[s] = imp[s] * math.exp(delta) * (1 + 0.8 * sd / 0.3) * damp
        parts[s] = {"importance": imp[s], "gap": gap, "sd": sd, "damp": damp, "mean_rating": mean_rating}
    z = sum(raw.values()) or 1.0
    shares = {s: v / z for s, v in raw.items()}
    for _ in range(10):  # plafonne puis redistribue le surplus aux autres compétences
        over = {s for s, v in shares.items() if v > MAX_SHARE + 1e-9}
        if not over:
            break
        surplus = sum(shares[s] - MAX_SHARE for s in over)
        rest = sum(v for s, v in shares.items() if s not in over) or 1.0
        shares = {s: MAX_SHARE if s in over else v + surplus * v / rest for s, v in shares.items()}
    for s in CATEGORIES:
        parts[s]["share"] = shares[s]
    return shares, parts


def attempts_expected(role: str, p: float) -> float:
    if role == "Échauffement":
        return 1.0
    if role == "Défi":
        return 2.5
    if role == "Révision":
        return 1.3
    if role == "Test de niveau":
        return 1.2
    return min(2.5, 1.0 + 1.5 * (1 - p))


def explain(role: str, skill: str, ctx: Context, shares: dict, parts: dict, h: History, p: float) -> str:
    label = LABELS.get(skill, skill)
    if role == "Échauffement":
        return f"Une map facile ({p:.0%} de chances d'atteindre l'objectif) pour te chauffer sans pression."
    if role == "Test de niveau":
        return (f"{label} est encore mal mesurée ({ctx.games[skill]:.0f} parties) : cette map sert à la situer, "
                "joue-la normalement.")
    if role == "Défi":
        return "Juste au-dessus du niveau où tu finis une map sur deux : elle teste tes limites."
    if role == "Révision":
        return (f"{label} est la compétence que tu as le moins travaillée ces 7 derniers jours par rapport à son "
                "importance : on l'entretient avant qu'elle ne retombe.")
    if role == "Examen":
        return "Examen pour valider le niveau suivant."
    rank = sorted(shares, key=shares.get, reverse=True).index(skill) + 1
    part = parts[skill]
    bits = [f"{label} est ta priorité n°{rank} ({part['share']:.0%} du programme)"]
    if part["gap"] > 0.25:
        bits.append(f"ta note ({fmt.stars(ctx.ratings[skill])}) est sous la moyenne de tes compétences "
                    f"({fmt.stars(part['mean_rating'])})")
    elif part["sd"] > 0.22:
        bits.append("sa note est encore incertaine")
    else:
        bits.append(f"elle compte dans {part['importance']:.0%} des maps du niveau suivant")
    if h.attempts and not h.met:
        bits.append(f"tu l'as déjà essayée {h.attempts} fois : la rejouer consolide les passages difficiles")
    return "; ".join(bits).capitalize() + "."


# --- Cœur du planificateur -------------------------------------------------------------------

def plan(ctx: Context, cands: list[Cand]) -> tuple[list[Item], dict]:
    """Renvoie (maps ordonnées, résumé). Pure : ne lit ni n'écrit rien."""
    def p_finish_of(c: Cand) -> float | None:
        if ctx.clear_level is None:
            return None
        return model.finish_prob(c.stars, c.hp, ctx.clear_level, ctx.clear_scale, ctx.hp_k)

    cands = [c for c in cands if c.beatmap_id not in ctx.exclude
             and ctx.level - 1.6 <= c.stars <= ctx.level + 1.3 and c.length_s > 0
             and (p_finish_of(c) is None or p_finish_of(c) >= PLAN_FINISH_FLOOR)]
    shares, parts = skill_shares(ctx, cands)
    summary = {"shares": shares, "parts": parts, "level": ctx.level, "budget": ctx.budget_min,
               "target_finish": ctx.target_finish, "target_meet": ctx.target_meet}
    if not cands:
        return [], summary

    p_cache = {c.beatmap_id: model.p_map(c.weights, ctx.ratings, effective_stars(c.stars, c.hp), ctx.calib)
               for c in cands}
    hist = lambda c: ctx.history.get(c.beatmap_id, History())
    chosen: list[Item] = []
    used_sets: set[int] = set()
    creators: Counter = Counter()
    load = {s: 0.0 for s in CATEGORIES}
    taken: set[int] = set()

    def top_skill(c: Cand) -> str:
        return max(c.weights, key=lambda s: shares.get(s, 0) * c.weights[s])

    def usable(c: Cand) -> bool:
        return (c.beatmap_id not in taken and c.set_id not in used_sets
                and (not c.creator or creators[c.creator] < MAX_PER_CREATOR))

    def take(c: Cand, role: str, skill: str, level_c: int | None = None) -> Item:
        p = p_cache[c.beatmap_id]
        tier = ctx.tier_open.get(c.beatmap_id)
        item = Item(
            role=role, category=skill,
            beatmap_id=c.beatmap_id,
            level_c=level_c if level_c is not None else (tier[1] if tier else progression.floor_level(c.stars)),
            p=p, minutes=model.minutes_for(c.length_s, attempts_expected(role, p)),
            reason=explain(role, skill, ctx, shares, parts, hist(c), p), share=shares.get(skill, 0.0),
        )
        chosen.append(item)
        taken.add(c.beatmap_id)
        used_sets.add(c.set_id)
        if c.creator:
            creators[c.creator] += 1
        for s, w in c.weights.items():
            load[s] += w
        return item

    def base(c: Cand) -> float:
        return (model.quality(c.playcount, c.favourites) * model.duration_factor(c.length_s, c.weights.get("stamina", 0))
                * freshness(hist(c)) * (1 + 0.10 * noise(ctx.seed, c.beatmap_id)))

    B = ctx.budget_min
    warm_budget = 0.0 if ctx.warmed_up else 0.08 * B
    stretch_budget = 0.0 if "Défi" in ctx.done_roles else 0.17 * B
    review_budget = 0.0 if "Révision" in ctx.done_roles else 0.12 * B

    # 0. Examen : s'il est débloqué, c'est la priorité.
    by_id = {c.beatmap_id: c for c in cands}
    exam_minutes = 0.0
    for bid in ctx.exam:
        if bid in by_id and usable(by_id[bid]):
            it = take(by_id[bid], "Examen", "exam", level_c=ctx.exam_level_c)
            exam_minutes += it.minutes

    # 1. Tests de niveau : les compétences encore mal mesurées (information maximale vers 50 % de réussite).
    test_minutes = 0.0
    unmeasured = sorted((s for s in CATEGORIES if ctx.games[s] < CALIBRATED_GAMES), key=lambda s: ctx.games[s])
    for s in unmeasured[:MAX_TESTS]:
        pool = [c for c in cands if usable(c) and c.weights.get(s, 0) >= 0.4
                and c.length_s <= 300 and model.duration_factor(c.length_s) >= 0.9]
        if not pool:
            continue
        def info(c, s=s):
            ps = model.p_skill(ctx.ratings[s], c.stars, ctx.calib)
            return c.weights[s] * model.bump(ps, 0.5, 0.2) * base(c)
        it = take(max(pool, key=info), "Test de niveau", s)
        test_minutes += it.minutes

    # 2. Travail ciblé (glouton avec rendements décroissants) sur le temps restant.
    main_budget = max(min(8.0, B), B - warm_budget - stretch_budget - review_budget - exam_minutes - test_minutes)

    def learn(c: Cand) -> float:
        """Difficulté productive : une map que tu finis la plupart du temps et dont l'objectif est jouable."""
        p_meet = p_cache[c.beatmap_id]
        if ctx.clear_level is None:
            return model.bump(p_meet, ctx.target_meet + 0.1, 0.25)
        return (model.bump(p_finish_of(c), ctx.target_finish, FINISH_WIDTH)
                * model.bump(p_meet, ctx.target_meet, MEET_WIDTH))

    def main_value(c: Cand) -> float:
        p = p_cache[c.beatmap_id]
        skills = sum(shares.get(s, 0) * w * DIMINISH ** load[s] for s, w in c.weights.items())
        tier = TIER_BONUS if c.beatmap_id in ctx.tier_open else 1.0
        minutes = model.minutes_for(c.length_s, attempts_expected("Entraînement", p))
        return skills * learn(c) * base(c) * tier / math.sqrt(minutes)

    pool = [c for c in cands if usable(c)]

    def add_main(limit: float, used) -> None:
        """Ajoute des maps de travail ciblé tant que `used()` + la map reste dans `limit` minutes."""
        while used() < limit - 1.5:
            ranked = sorted((c for c in pool if usable(c)), key=main_value, reverse=True)
            pick = None
            for c in ranked[:60]:
                est = model.minutes_for(c.length_s, attempts_expected("Entraînement", p_cache[c.beatmap_id]))
                if used() + est <= limit * 1.2 + 1.0 or not any(i.role in ("Entraînement", "Objectif") for i in chosen):
                    pick = c
                    break
            if pick is None:
                return
            role = "Objectif" if pick.beatmap_id in ctx.tier_open else "Entraînement"
            take(pick, role, ctx.tier_open[pick.beatmap_id][0] if role == "Objectif" else top_skill(pick))

    work_minutes = lambda: sum(i.minutes for i in chosen if i.role in ("Entraînement", "Objectif"))
    add_main(main_budget, work_minutes)

    # 3. Défi : juste au-dessus du niveau où tu finis une map sur deux.
    clear_level = ctx.clear_level if ctx.clear_level is not None else ctx.level
    def stretch_value(c: Cand) -> float:
        p_clear = p_finish_of(c) if ctx.clear_level is not None else model.sigmoid((clear_level - c.stars) / ctx.clear_scale)
        skills = sum(shares.get(s, 0) * w * DIMINISH ** load[s] for s, w in c.weights.items())
        return skills * model.bump(p_clear, 0.4, 0.15) * base(c) * (1.0 if c.length_s <= 300 else 0.6)
    pool = [c for c in cands if usable(c) and c.stars >= clear_level - 0.1 and p_cache[c.beatmap_id] <= 0.55]
    if pool and B >= 25 and "Défi" not in ctx.done_roles:
        c = max(pool, key=stretch_value)
        take(c, "Défi", top_skill(c))

    # 4. Révision espacée : la compétence la moins travaillée récemment parmi les moins prioritaires.
    top2 = sorted(shares, key=shares.get, reverse=True)[:2]
    # La révision apporte une compétence différente de celles déjà travaillées dans la séance (variété).
    review_skills = [s for s in CATEGORIES if s not in top2 and ctx.games[s] >= CALIBRATED_GAMES and load[s] < 0.8]
    if review_skills and B >= 35 and "Révision" not in ctx.done_roles:
        total7 = sum(ctx.load7d.values()) or 1.0
        s = min(review_skills, key=lambda k: (ctx.load7d.get(k, 0.0) / total7) / parts[k]["importance"])
        pool = [c for c in cands if usable(c) and c.weights.get(s, 0) >= 0.4 and c.length_s <= 240]
        if pool:
            def review_value(c, s=s):
                return c.weights[s] * model.bump(p_cache[c.beatmap_id], 0.72, 0.12) * base(c)
            take(max(pool, key=review_value), "Révision", s)

    # 5. Échauffement : facile, court, dans ta compétence la plus solide (placé en tête de séance).
    #    Une seule fois par jour : si tu as déjà joué, tu es chaud, la suite reste du travail.
    strongest = model.strongest_skill(ctx.ratings, ctx.games)
    pool = [c for c in cands if usable(c) and c.length_s <= 150 and c.stars >= ctx.level - 1.3
            and c.weights.get(strongest, 0) >= 0.3]
    if pool and B >= 25 and not ctx.warmed_up:
        def warm_value(c):
            return c.weights[strongest] * model.bump(p_cache[c.beatmap_id], 0.88, 0.08) * base(c)
        take(max(pool, key=warm_value), "Échauffement", strongest)

    # 6. Si le temps réservé à l'échauffement, au défi et à la révision n'a pas été entièrement utilisé,
    #    on le consacre au travail ciblé : toute la séance demandée est utilisée.
    add_main(B, lambda: sum(i.minutes for i in chosen))

    # Ordre : échauffement, tests, examen, travail (du plus accessible au plus dur), défi, révision.
    order = {"Échauffement": 0, "Test de niveau": 1, "Examen": 2, "Objectif": 3, "Entraînement": 3, "Défi": 4, "Révision": 5}
    chosen.sort(key=lambda i: (order[i.role], -i.p if order[i.role] == 3 else 0))
    summary["minutes"] = round(sum(i.minutes for i in chosen), 1)
    return chosen, summary


# --- Couche base de données ----------------------------------------------------------------

def load_candidates(conn) -> list[Cand]:
    placeholders = ",".join("?" * len(progression.PLAYABLE_STATUSES))
    rows = conn.execute(
        f"""SELECT a.beatmap_id, a.stars, a.weights, a.attrs, b.beatmapset_id, b.creator, b.playcount, b.favourites
            FROM map_attrs a JOIN beatmaps b ON b.beatmap_id = a.beatmap_id
            WHERE a.mods_key = '' AND b.status IN ({placeholders})""",
        progression.PLAYABLE_STATUSES,
    ).fetchall()
    out = []
    for r in rows:
        attrs = json.loads(r["attrs"])
        out.append(Cand(r["beatmap_id"], r["beatmapset_id"] or 0, r["creator"] or "", r["stars"],
                        json.loads(r["weights"]), attrs.get("length_s") or 0.0, r["playcount"] or 0,
                        r["favourites"] or 0, attrs.get("hp", HP_REF)))
    return out


def load_history(conn, cands: list[Cand], day: str) -> dict[int, History]:
    by_id = {c.beatmap_id: c for c in cands}
    today = datetime.fromisoformat(day).date()
    out = {}
    for r in conn.execute(
        """SELECT beatmap_id, count(*) AS n, max(ended_at) AS last FROM scores
           WHERE countable = 1 AND source != 'inferred_fail' GROUP BY beatmap_id"""
    ):
        c = by_id.get(r["beatmap_id"])
        if not c:
            continue
        top = max(c.weights, key=c.weights.get)
        best = progression.best_result(conn, c.beatmap_id, CRITERIA[top])
        days = (today - datetime.fromisoformat(db.local_day(r["last"])).date()).days
        out[c.beatmap_id] = History(r["n"], bool(best and best["met"]), float(days), best["accuracy"] if best else None)
    return out


def load_loads(conn, day: str) -> tuple[dict, dict]:
    """Sollicitation récente de chaque compétence : somme des poids des parties des 48 h et des 7 jours."""
    now = datetime.fromisoformat(day) + timedelta(days=1)  # fin de la journée
    l2 = {s: 0.0 for s in CATEGORIES}
    l7 = {s: 0.0 for s in CATEGORIES}
    since = (now - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00Z")
    for r in conn.execute(
        """SELECT s.ended_at, a.weights FROM scores s
           JOIN map_attrs a ON a.beatmap_id = s.beatmap_id AND a.mods_key = s.mods_key
           WHERE s.countable = 1 AND s.source != 'inferred_fail' AND s.ended_at >= ?""", (since,)
    ):
        age = (now - datetime.fromisoformat(db.local_day(r["ended_at"]))).days
        for s, w in json.loads(r["weights"]).items():
            l7[s] += w
            if age <= 2:
                l2[s] += w
    return l2, l7


def typical_session_minutes(conn) -> int:
    """Durée habituelle de tes séances, déduite de l'espacement de tes parties."""
    times = [datetime.fromisoformat(r["ended_at"].replace("Z", "+00:00")) for r in conn.execute(
        "SELECT DISTINCT ended_at FROM scores WHERE source IN ('recent', 'lazer_local') ORDER BY ended_at")]
    sessions, current = [], []
    for t in times:
        if current and (t - current[-1]) > timedelta(minutes=25):
            sessions.append(current)
            current = []
        current.append(t)
    if current:
        sessions.append(current)
    lengths = [(s[-1] - s[0]).total_seconds() / 60 + 3 for s in sessions if len(s) >= 3][-20:]
    if len(lengths) < 3:
        return DEFAULT_MINUTES
    return int(max(30, min(90, round(statistics.median(lengths) / 5) * 5)))


def settings(conn) -> dict:
    return {
        "minutes": db.get_meta(conn, "session_minutes") or typical_session_minutes(conn),
        "auto_minutes": db.get_meta(conn, "session_minutes") is None,
        "intensity": db.get_meta(conn, "intensity") or DEFAULT_INTENSITY,
    }


def build_context(conn, ratings: dict, day: str, exclude: set, cands: list[Cand]) -> Context:
    clear = db.get_meta(conn, "clear_level")
    mean = sum(ratings[s]["rating"] for s in CATEGORIES) / len(CATEGORIES)
    cfg = settings(conn)
    variant = db.get_meta(conn, "session_variant", {}).get(day, 0)
    l2, l7 = load_loads(conn, day)
    tier_open, exam = {}, []
    for cat, validated in progression.load_progress(conn).items():
        level = validated + progression.STEP_C
        for r in conn.execute("SELECT beatmap_id FROM tier_maps WHERE category = ? AND level_c = ?", (cat, level)):
            res = progression.best_result(conn, r["beatmap_id"], CRITERIA[cat])
            if not (res and res["met"]):
                tier_open[r["beatmap_id"]] = (cat, level)
    state = progression.exam_state(conn)
    if state["unlocked"]:
        for r in conn.execute("SELECT beatmap_id FROM tier_maps WHERE category = 'exam' AND level_c = ?",
                              (state["target_c"],)):
            res = progression.best_result(conn, r["beatmap_id"], progression.EXAM_CRITERION)
            if not (res and res["met"]):
                exam.append(r["beatmap_id"])
    return Context(
        ratings={s: ratings[s]["rating"] for s in CATEGORIES},
        games={s: ratings[s]["games"] for s in CATEGORIES},
        level=clear["level"] if clear else mean + 0.4,
        budget_min=float(cfg["minutes"]),
        target_finish=INTENSITY[cfg["intensity"]][0], target_meet=INTENSITY[cfg["intensity"]][1],
        seed=f"{day}-{variant}", calib=model.load_calibration(conn),
        clear_level=clear["level"] if clear else None, clear_scale=(clear or {}).get("scale", DEFAULT_CLEAR_SCALE),
        hp_k=(clear or {}).get("hp_k", 0.0),
        load2d=l2, load7d=l7, history=load_history(conn, cands, day),
        tier_open=tier_open, exam=exam, exam_level_c=state["target_c"], exclude=set(exclude),
    )


def _plays_today(conn, day: str) -> int:
    return sum(1 for r in conn.execute("SELECT ended_at FROM scores WHERE source != 'inferred_fail' "
                                       "AND ended_at >= ?", ((datetime.fromisoformat(day) - timedelta(days=1))
                                                              .strftime("%Y-%m-%dT00:00:00Z"),))
               if db.local_day(r["ended_at"]) == day)


def _minutes_played_today(conn, day: str) -> float:
    """Temps réellement passé à jouer aujourd'hui (programme ou non) : durée de chaque série de parties."""
    times = sorted(datetime.fromisoformat(r["ended_at"].replace("Z", "+00:00")) for r in conn.execute(
        "SELECT ended_at FROM scores WHERE source != 'inferred_fail' AND ended_at >= ?",
        ((datetime.fromisoformat(day) - timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z"),))
        if db.local_day(r["ended_at"]) == day)
    total, start = 0.0, None
    for prev, t in zip([None] + times, times):
        if prev is None or (t - prev) > timedelta(minutes=25):
            if start is not None:
                total += (prev - start).total_seconds() / 60 + 2  # + la première map de la série
            start = t
    if start is not None:
        total += (times[-1] - start).total_seconds() / 60 + 2
    return total


def session_budget(conn, day: str) -> float:
    """Durée de la séance du jour : la durée réglée, plus les rallonges demandées."""
    return float(settings(conn)["minutes"]) + db.get_meta(conn, "session_extra", {}).get(day, 0)


def add_session_time(conn, day: str, minutes: int) -> None:
    """Rallonge la séance du jour de `minutes` minutes à partir de maintenant : des maps sont ajoutées à la fin."""
    current = db.get_meta(conn, "session_extra", {}).get(day, 0)
    overrun = max(0.0, _minutes_played_today(conn, day) - session_budget(conn, day))  # temps déjà dépassé
    db.set_meta(conn, "session_extra", {day: round(current + overrun + minutes)})
    conn.commit()


TRIM_TOLERANCE_MIN = 2.0  # on ne retire une map prévue que si la séance déborde de plus de 2 min


def fit_pending(pending_minutes: list[float], used: float, budget: float) -> int:
    """Combien de maps prévues (non jouées) garder, dans l'ordre, pour tenir dans le temps qui reste."""
    keep = len(pending_minutes)
    while keep and used + sum(pending_minutes[:keep]) > budget + TRIM_TOLERANCE_MIN:
        keep -= 1
    return keep


def build_session(conn, api, ratings: dict, day: str, log=print) -> None:
    """Construit ou ajuste la séance du jour.

    Le programme reste STABLE dans la journée : une partie jouée, une map écartée ou une durée changée ne font
    que retirer la map concernée et compléter (ou raccourcir) la fin de la liste. On ne retire tout au sort que
    pour un nouveau jour, un changement de difficulté, ou « Proposer d'autres maps ». Les maps jouées restent en
    tête.
    """
    cfg = settings(conn)
    ignored = progression.ignored_maps(conn)
    base = [PLAN_FORMAT, day, cfg["intensity"], db.get_meta(conn, "session_variant", {}).get(day, 0)]
    key = base + [session_budget(conn, day), _plays_today(conn, day), len(ignored)]
    stored = db.get_meta(conn, "session_key")
    existing = conn.execute("SELECT * FROM session_plan WHERE day = ? ORDER BY pos", (day,)).fetchall()
    if existing and stored == key:
        return
    full = not existing or not stored or stored[:len(base)] != base

    played = [r for r in existing if _played_today(conn, r["beatmap_id"], day)]
    played_ids = {r["beatmap_id"] for r in played}
    pending = [] if full else [r for r in existing
                               if r["beatmap_id"] not in played_ids and r["beatmap_id"] not in ignored]
    budget = session_budget(conn, day)
    # Le temps déjà passé : le plus grand entre l'estimation des maps jouées et le temps réel (téléchargements,
    # essais hors programme…).
    used = max(sum((r["minutes"] or 0) for r in played), _minutes_played_today(conn, day))
    pending = pending[:fit_pending([r["minutes"] or 0 for r in pending], used, budget)]
    remaining = budget - used - sum((r["minutes"] or 0) for r in pending)

    items, summary = [], None
    if remaining >= 3.0:
        cands = load_candidates(conn)
        exclude = {r["beatmap_id"] for r in existing} | ignored
        ctx = build_context(conn, ratings, day, exclude=exclude, cands=cands)
        ctx.budget_min = remaining
        ctx.warmed_up = bool(played or pending) or _plays_today(conn, day) > 0
        ctx.done_roles = {r["role"] for r in played + pending}
        items, summary = plan(ctx, cands)

    conn.execute("DELETE FROM session_plan WHERE day = ?", (day,))
    rows = [(day, i, r["role"], r["category"], r["level_c"], r["beatmap_id"], r["reason"], r["p"], r["minutes"], r["share"])
            for i, r in enumerate(played + pending)]
    rows += [(day, len(rows) + i, it.role, it.category, it.level_c, it.beatmap_id, it.reason, it.p, it.minutes, it.share)
             for i, it in enumerate(items)]
    conn.executemany("INSERT INTO session_plan (day, pos, role, category, level_c, beatmap_id, reason, p, minutes, share) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    if summary and (full or not db.get_meta(conn, "plan_summary")):
        summary["day"] = day
        summary["intensity"] = cfg["intensity"]
        summary["budget"] = float(cfg["minutes"])
        summary["shares"] = {s: round(v, 4) for s, v in summary["shares"].items()}
        summary["parts"] = {s: {k: round(v, 4) for k, v in p.items()} for s, p in summary["parts"].items()}
        db.set_meta(conn, "plan_summary", summary)
    db.set_meta(conn, "session_key", key)
    conn.commit()


def _played_today(conn, beatmap_id: int, day: str) -> bool:
    return bool(progression.played_on(conn, beatmap_id, day))


def ignore_map(conn, beatmap_id: int, day: str) -> None:
    """« Ne plus me proposer cette map » : elle sort du programme (et des paliers) ; une autre est ajoutée en fin
    de liste au prochain affichage. Déjà jouée aujourd'hui, elle reste dans les maps terminées."""
    progression.ignore_map(conn, beatmap_id)
    if not _played_today(conn, beatmap_id, day):
        conn.execute("DELETE FROM session_plan WHERE day = ? AND beatmap_id = ?", (day, beatmap_id))
    conn.commit()


def forecast_check(conn, today: str, days: int = 14) -> dict:
    """Le programme tient-il ses promesses ? Compare, sur les jours passés, les objectifs atteints aux prévisions.

    `p` est la chance d'atteindre l'objectif en UN essai : on ne juge donc que le premier essai du jour sur chaque
    map. Compter « au moins une réussite sur tous les essais » gonflerait les réussites et masquerait un modèle
    trop optimiste.
    """
    since = (datetime.fromisoformat(today) - timedelta(days=days)).date().isoformat()
    rows = conn.execute(
        "SELECT day, beatmap_id, category, role, p FROM session_plan WHERE day < ? AND day >= ? AND p IS NOT NULL",
        (today, since)).fetchall()
    n = met = predicted = 0.0
    for r in rows:
        if r["role"] in ("Échauffement", "Test de niveau"):
            continue
        plays = progression.played_on(conn, r["beatmap_id"], r["day"])
        if not plays:
            continue
        first = plays[-1]  # played_on : du plus récent au plus ancien
        crit = progression.criterion_for(r["category"])
        n += 1
        predicted += r["p"]
        met += progression.meets(bool(first["passed"]), first["accuracy"], first["n_miss"],
                                 progression.n_objects_of(conn, r["beatmap_id"]), crit)
    return {"n": int(n), "met": int(met), "predicted": predicted, "any_rows": bool(rows)}


def reset_session(conn, day: str) -> None:
    """Pioche d'autres maps (même logique, autre variante) : les maps déjà jouées restent en tête."""
    variant = db.get_meta(conn, "session_variant", {})
    db.set_meta(conn, "session_variant", {day: variant.get(day, 0) + 1})
    db.del_meta(conn, "session_key")
    conn.commit()
