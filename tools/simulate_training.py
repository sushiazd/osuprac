"""Compare des méthodes d'entraînement sur un apprenant simulé (outil de mise au point).

Usage : python tools/simulate_training.py [--days 30] [--seeds 12]

L'apprenant a six compétences « vraies » que le planificateur ne voit pas : il n'observe que des
résultats (objectif atteint ou non, map finie ou non) et en déduit des notes bruitées, comme dans la
vraie vie. Son profil de départ est celui de ta base. Chaque méthode planifie une séance par jour.

Limite assumée : on ne connaît pas la vraie courbe d'apprentissage. On compare donc les méthodes sous
plusieurs hypothèses (progresse mieux près de 70 % de réussite, sur plus facile, sur plus dur, ou
indépendamment de la difficulté). Une bonne méthode doit rester bonne sous toutes.
"""
import argparse
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from osuprog import db, model, planner, rating  # noqa: E402
from osuprog.planner import Cand, Context, History  # noqa: E402
from osuprog.skills import CATEGORIES  # noqa: E402

TRUE_SCALE = 0.5
CLEAR_SHIFT = 0.55   # finir une map est plus facile qu'atteindre l'objectif de précision (≈ ce qu'on mesure)
CLEAR_SCALE = 0.56
SATURATION = 10.0     # après ~10 parties pondérées sur une compétence dans la journée, le gain est divisé par deux
OPTIMISM = -0.4      # le modèle d'Elo est un peu trop optimiste sur « objectif atteint » (≈ ce qu'on mesure)

CURVES = {  # (finir visé, objectif visé, largeur finir, largeur objectif) ; None = indépendant de la difficulté
    "optimum ~70 %": (0.72, 0.45, 0.20, 0.30),
    "optimum facile": (0.85, 0.65, 0.20, 0.30),
    "optimum dur": (0.55, 0.30, 0.20, 0.30),
    "indifférent": None,
}


class Learner:
    def __init__(self, rng, start, curve, eta=0.003):
        self.rng = rng
        self.t = {s: start[s] + 0.25 + rng.gauss(0, 0.1) for s in CATEGORIES}
        self.curve, self.eta = curve, eta
        # état estimé (ce que voit le planificateur)
        base = sum(start.values()) / len(start)
        self.r = {s: base - 0.3 for s in CATEGORIES}
        self.g = {s: 0.0 for s in CATEGORIES}
        self.events, self.clear_points = [], []
        self.history: dict[int, History] = {}
        self.log: list[tuple[int, dict]] = []   # (jour, poids) de chaque partie
        self.today = {s: 0.0 for s in CATEGORIES}

    # --- vérité cachée ---
    def p_meet(self, w, stars):
        return sum(x * model.sigmoid((self.t[s] - stars) / TRUE_SCALE + OPTIMISM) for s, x in w.items()) / sum(w.values())

    def p_finish(self, w, stars):
        return sum(x * model.sigmoid((self.t[s] + CLEAR_SHIFT - stars) / CLEAR_SCALE) for s, x in w.items()) / sum(w.values())

    def efficiency(self, pm, pf):
        if self.curve is None:
            return 1.0
        fs, ms, wf, wm = self.curve
        return model.bump(pf, fs, wf) * model.bump(pm, ms, wm)

    def play(self, c: Cand, day: int, fatigue: float):
        pm, pf = self.p_meet(c.weights, c.stars), self.p_finish(c.weights, c.stars)
        meet = self.rng.random() < pm
        finish = meet or self.rng.random() < pf
        gain = self.eta * self.efficiency(pm, pf) * fatigue
        for s in CATEGORIES:
            # effet d'espacement : la pratique massée sur une compétence dans la journée rapporte de moins en moins
            saturation = 1 / (1 + self.today[s] / SATURATION)
            self.t[s] += gain * (c.weights.get(s, 0.0) + 0.08) * saturation
        for s, w in c.weights.items():
            self.today[s] += w
        # observation -> notes estimées (mêmes règles que la production)
        for s, w in c.weights.items():
            p = model.sigmoid((self.r[s] - c.stars) / rating.SCALE)
            k = 0.08 + 0.52 / (1 + self.g[s] / 8)
            self.r[s] += k * w * ((1.0 if meet else 0.0) - p)
            self.g[s] += w
            self.events.append((model.logit(p), 1.0 if meet else 0.0, w))
        self.clear_points.append((c.stars, 1.0 if finish else 0.0))
        h = self.history.setdefault(c.beatmap_id, History())
        h.attempts += 1
        h.met = h.met or meet
        h.days_since = 0.0
        self.log.append((day, c.weights))

    def end_day(self, day: int):
        self.today = {s: 0.0 for s in CATEGORIES}
        loads = defaultdict(float)
        for d, w in self.log:
            if d == day:
                for s, x in w.items():
                    loads[s] += x
        for s in CATEGORIES:
            if loads[s] < 1.0:
                self.t[s] -= 0.003   # oubli lent des compétences non travaillées
        for h in self.history.values():
            h.days_since = (h.days_since or 0.0) + 1

    # --- état observé ---
    def context(self, day: int, minutes: float, seed: str, intensity: str) -> Context:
        clear = rating.fit_clear_level(self.clear_points[-250:])
        calib = (0.0, 1.0)
        if len(self.events) >= 40:
            calib = model.fit_calibration(self.events[-300:])
        mean = sum(self.r.values()) / len(self.r)
        l2 = {s: 0.0 for s in CATEGORIES}
        l7 = {s: 0.0 for s in CATEGORIES}
        for d, w in self.log:
            if day - d <= 6:
                for s, x in w.items():
                    l7[s] += x
                    if day - d <= 1:
                        l2[s] += x
        f, m = planner.INTENSITY[intensity]
        return Context(
            ratings=dict(self.r), games=dict(self.g), level=clear["level"] if clear else mean + 0.4,
            budget_min=minutes, target_finish=f, target_meet=m, seed=seed, calib=calib,
            clear_level=clear["level"] if clear else None, clear_scale=clear["scale"] if clear else 0.55,
            load2d=l2, load7d=l7, history=self.history,
        )


# --- Méthodes comparées ------------------------------------------------------------------------

def attempts_for(item) -> int:
    return max(1, round(planner.attempts_expected(item.role, item.p)))


def run_plan(c_list, minutes, attempts=2):
    """Joue des maps dans l'ordre jusqu'à épuiser le temps."""
    out, used = [], 0.0
    for c in c_list:
        cost = model.minutes_for(c.length_s, attempts)
        if used + cost > minutes * 1.15 and out:
            break
        out.append((c, attempts))
        used += cost
    return out


def policy_planner(intensity, **params):
    def policy(L, cands, day, minutes, rng, seed):
        saved = {k: getattr(planner, k) for k in params}
        for k, v in params.items():
            setattr(planner, k, v)
        try:
            ctx = L.context(day, minutes, seed, intensity)
            items, _ = planner.plan(ctx, cands)
        finally:
            for k, v in saved.items():
                setattr(planner, k, v)
        by_id = {c.beatmap_id: c for c in cands}
        return [(by_id[i.beatmap_id], attempts_for(i)) for i in items]
    return policy


def level_of(L, minutes_unused=None):
    clear = rating.fit_clear_level(L.clear_points[-250:])
    return clear["level"] if clear else sum(L.r.values()) / len(L.r) + 0.4


def policy_band(lo, hi):
    def policy(L, cands, day, minutes, rng, seed):
        level = level_of(L)
        pool = [c for c in cands if level + lo <= c.stars <= level + hi and c.length_s > 0]
        rng.shuffle(pool)
        return run_plan(pool, minutes)
    return policy


def policy_old(L, cands, day, minutes, rng, seed):
    """Ancienne méthode : 2 compétences les plus faibles, maps à ton niveau de note, un défi."""
    order = sorted(CATEGORIES, key=lambda s: L.r[s])
    out = []
    for s, n in ((order[0], 3), (order[1], 2)):
        pool = [c for c in cands if c.weights.get(s, 0) >= 0.4 and L.r[s] - 0.1 <= c.stars < L.r[s] + 0.35]
        pool.sort(key=lambda c: -(c.weights[s] + 0.04 * math.log10(c.playcount + 1) + 0.2 * rng.random()))
        out += [(c, 2) for c in pool[:n]]
    level = level_of(L)
    pool = [c for c in cands if level + 0.2 <= c.stars <= level + 0.5]
    if pool:
        out.append((rng.choice(pool), 2))
    return out


def policy_blocked(L, cands, day, minutes, rng, seed):
    """Tout sur le point faible, sans variété, à ~50 % de réussite."""
    s = min(CATEGORIES, key=lambda k: L.r[k])
    pool = [c for c in cands if c.weights.get(s, 0) >= 0.4 and abs(model.p_skill(L.r[s], c.stars) - 0.55) < 0.2]
    rng.shuffle(pool)
    return run_plan(pool, minutes)


# --- Boucle ------------------------------------------------------------------------------------

def simulate(policy, curve, start, cands, imp, days, minutes, seed):
    rng = random.Random(seed)
    L = Learner(rng, start, CURVES[curve])
    t0 = dict(L.t)
    for day in range(days):
        plan = policy(L, cands, day, minutes, rng, f"{seed}-{day}")
        fatigue = 1.0
        for c, attempts in plan:
            for _ in range(attempts):
                L.play(c, day, fatigue)
                fatigue = max(0.55, fatigue - 0.012)   # la qualité de pratique baisse au fil de la séance
        L.end_day(day)
    gains = {s: L.t[s] - t0[s] for s in CATEGORIES}
    return {
        "poids": sum(imp[s] * gains[s] for s in CATEGORIES),   # gain pondéré par l'importance des compétences
        "moy": statistics.mean(gains.values()),
        "min": min(gains.values()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--minutes", type=float, default=40)
    args = ap.parse_args()

    conn = db.connect()
    cands = planner.load_candidates(conn)
    ratings = rating.load(conn)
    start = {s: ratings[s]["rating"] for s in CATEGORIES}
    clear = rating.clear_level(conn)
    band = [c for c in cands if clear["level"] - 0.25 <= c.stars <= clear["level"] + 0.75]
    tot = {s: sum(c.weights.get(s, 0) for c in band) for s in CATEGORIES}
    imp = {s: v / sum(tot.values()) for s, v in tot.items()}
    print(f"{len(cands)} maps ; profil de départ {', '.join(f'{s} {v:.2f}' for s, v in start.items())}")
    print(f"{args.days} jours × séances de {args.minutes:.0f} min × {args.seeds} apprenants par cas\n")

    methods = {
        "planificateur (équilibré)": policy_planner("équilibré"),
        "planificateur (intense)": policy_planner("intense"),
        "planificateur (confort)": policy_planner("confort"),
        "  sans plafond de part": policy_planner("équilibré", MAX_SHARE=1.0),
        "  sans variété": policy_planner("équilibré", DIMINISH=1.0),
        "ancienne méthode (points faibles)": policy_old,
        "point faible en bloc": policy_blocked,
        "maps au hasard (niveau ±0,5)": policy_band(-0.5, 0.5),
        "maps trop dures (+0,4 à +1)": policy_band(0.4, 1.0),
        "maps trop faciles (-1,4 à -0,8)": policy_band(-1.4, -0.8),
    }
    for curve in CURVES:
        print(f"== Hypothèse d'apprentissage : {curve}")
        print(f"{'méthode':<36}{'gain pondéré':>13}{'gain moyen':>12}{'pire compétence':>17}")
        rows = []
        for name, pol in methods.items():
            res = [simulate(pol, curve, start, cands, imp, args.days, args.minutes, seed) for seed in range(args.seeds)]
            rows.append((name, statistics.mean(r["poids"] for r in res), statistics.mean(r["moy"] for r in res),
                         statistics.mean(r["min"] for r in res)))
        best = max(r[1] for r in rows)
        for name, w, m, mn in rows:
            star = "  ←" if w == best else ""
            print(f"{name:<36}{w:>+12.3f}★{m:>+11.3f}★{mn:>+16.3f}★{star}")
        print()


if __name__ == "__main__":
    main()
