"""Compare des façons de classer les maps par compétence, sur TES parties (outil de mise au point).

Usage : python tools/eval_classifier.py

Pour chaque variante de classement, on rejoue tout l'historique dans l'ordre : avant chaque partie, le modèle
prédit tes chances d'atteindre l'objectif, puis apprend du résultat (comme rating.recompute). Un meilleur
classement doit donner de meilleures prédictions (Brier et log-loss plus bas). Deux mesures :

- par compétence (ce que le modèle apprend : critère propre à chaque compétence) ;
- par partie, avec un critère fixe (80 % / 2 % de miss) : indépendant du classement, donc plus juste pour comparer.
"""
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from osuprog import db, model, rating, skills  # noqa: E402
from osuprog.osufile import movement_features, osu_path  # noqa: E402
from osuprog.skills import CATEGORIES, CRITERIA, EXAM_CRITERION, meets, outcome, ramp  # noqa: E402

WARMUP = 60  # premières parties : le modèle se cale, on ne les note pas
MOVE_KEYS = ("jump_vel", "sharp_frac", "rhythm_irregular", "sv_changes")


def load(conn):
    maps = {}
    for r in conn.execute("SELECT beatmap_id, mods_key, stars, attrs FROM map_attrs"):
        maps[(r["beatmap_id"], r["mods_key"])] = (r["stars"], json.loads(r["attrs"]))
    moves = {}
    for bid in {b for b, _ in maps}:
        path = osu_path(bid)
        if path.exists():
            moves[bid] = movement_features(path.read_bytes())
    plays = conn.execute(
        """SELECT s.score_id, s.beatmap_id, s.mods_key, s.accuracy, s.n_miss, s.passed, s.ended_at FROM scores s
           JOIN map_attrs a ON a.beatmap_id = s.beatmap_id AND a.mods_key = s.mods_key
           WHERE s.countable = 1 ORDER BY s.ended_at""").fetchall()
    return maps, moves, plays


def typical_moves(maps, moves):
    """Médiane de chaque mesure par tranche de 0,5★ (sur les maps sans mod du catalogue)."""
    bands = defaultdict(list)
    for (bid, key), (stars, _) in maps.items():
        if key == "" and bid in moves:
            bands[round(stars * 2) / 2].append(moves[bid])
    table = {b: {k: statistics.median(m[k] for m in ms) for k in MOVE_KEYS} for b, ms in bands.items() if len(ms) >= 15}

    def at(stars):
        b = min(table, key=lambda x: abs(x - stars))
        return table[b]
    return at


def make_variants(typ):
    base = skills.classify

    def with_moves(attrs, move, use_aim=True, use_tech=True):
        """Comme classify, avec les mesures de mouvement ajoutées aux poids bruts d'Aim et de Tech."""
        stars = attrs["stars"]
        ratio = attrs["aim"] / attrs["speed"] if attrs["speed"] > 0 else 2.0
        rel = ratio / skills.typical(stars, 2)
        extra_sliders = (1 - attrs["slider_factor"]) - (1 - skills.typical(stars, 3))
        streams = ramp(attrs["stream_frac"], 0.10, 0.35)
        stream_stamina = (max(ramp(attrs["max_run"], 12, 32), ramp(attrs["long_stream_frac"], 0.04, 0.15))
                          * ramp(attrs["length_s"], 60, 150))
        stamina = max(stream_stamina, 0.7 * ramp(attrs["length_s"], 180, 330))
        t = typ(stars)
        aim = ramp(rel, 0.92, 1.12)
        if use_aim and move:
            jumpy = ramp(move["jump_vel"] / max(t["jump_vel"], 0.1), 1.1, 1.5)
            sharp = ramp(move["sharp_frac"] - t["sharp_frac"], 0.05, 0.2)
            aim = max(aim, 0.8 * jumpy, 0.6 * sharp)
        tech = ramp(extra_sliders, 0.015, 0.06)
        if use_tech and move:
            rhythm = ramp(move["rhythm_irregular"] - t["rhythm_irregular"], 0.08, 0.25)
            sv = ramp(move["sv_changes"] - t["sv_changes"], 0.5, 2.0)
            tech = max(tech, 0.8 * rhythm, 0.6 * sv)
        raw = {
            "aim": aim * (1 - 0.8 * streams),
            "speed": max(streams, 1 - ramp(rel, 0.74, 0.86)) * (1 - 0.5 * stamina),
            "stamina": stamina,
            "accuracy": ramp(attrs["od"] - skills.expected_od(stars), 0.25, 0.9),
            "reading": max(ramp(skills.expected_ar(stars) - attrs["ar"], 0.7, 1.5),
                           ramp(attrs["ar"] - skills.expected_ar(stars), 1.0, 2.0),
                           0.6 if attrs.get("hidden") else 0.0),
            "tech": tech,
        }
        lean = ramp(rel, 0.80, 1.08)
        raw["aim"] += 0.25 * lean
        raw["speed"] += 0.25 * (1 - lean)
        total = sum(raw.values())
        w = {c: v / total for c, v in raw.items() if v / total >= 0.1}
        total = sum(w.values())
        return {c: v / total for c, v in w.items()}

    return {
        "actuel (v5)": lambda a, m: base(a),
        "+ sauts/angles (Aim)": lambda a, m: with_moves(a, m, True, False),
        "+ rythme/vitesse sliders (Tech)": lambda a, m: with_moves(a, m, False, True),
        "+ les deux": lambda a, m: with_moves(a, m, True, True),
        "une seule note (référence)": lambda a, m: {"aim": 1.0},
    }


def evaluate(classify, maps, moves, plays, tracking_start):
    ratings = None
    games = {c: 0.0 for c in CATEGORIES}
    attempts = defaultdict(int)
    ev_b = ev_l = ev_w = 0.0
    pl_b = pl_l = 0.0
    pl_n = 0
    start_rows = []
    for r in plays:
        stars, attrs = maps[(r["beatmap_id"], r["mods_key"])]
        start_rows.append({"passed": r["passed"], "pp": 0, "stars": stars})
    start = rating.seed_rating(start_rows)
    ratings = {c: start for c in CATEGORIES}
    for idx, r in enumerate(plays):
        stars, attrs = maps[(r["beatmap_id"], r["mods_key"])]
        weights = classify(attrs, moves.get(r["beatmap_id"]))
        n_obj = attrs.get("n_objects") or rating.DEFAULT_OBJECTS
        eff = rating.effective_stars(stars, attrs.get("hp"))
        era = rating.SEED_WEIGHT if r["ended_at"] < tracking_start else 1.0
        key = (r["beatmap_id"], r["ended_at"][:10])
        attempts[key] += 1
        repeat = 1 / math.sqrt(attempts[key])
        scored = idx >= WARMUP and r["ended_at"] >= tracking_start
        if scored:
            p = model.p_map(weights, ratings, eff)
            y = float(meets(bool(r["passed"]), r["accuracy"], r["n_miss"], n_obj, EXAM_CRITERION))
            pl_b += (p - y) ** 2
            pl_l -= math.log(min(1 - 1e-6, max(1e-6, p if y else 1 - p)))
            pl_n += 1
        for cat, w in weights.items():
            p = rating.expected(ratings[cat], eff)
            o = outcome(bool(r["passed"]), r["accuracy"], r["n_miss"], n_obj, CRITERIA[cat])
            if scored:
                y = 1.0 if o >= 0.999 else 0.0
                ev_b += w * (p - y) ** 2
                ev_l -= w * math.log(min(1 - 1e-6, max(1e-6, p if y else 1 - p)))
                ev_w += w
            k = rating.K_MIN + (rating.K_MAX - rating.K_MIN) / (1 + games[cat] / rating.K_HALF_GAMES)
            ratings[cat] += k * w * era * repeat * (o - p)
            games[cat] += w * era * repeat
    return {"brier_comp": ev_b / ev_w, "logloss_comp": ev_l / ev_w, "brier_partie": pl_b / pl_n,
            "logloss_partie": pl_l / pl_n, "n": pl_n}


def main():
    conn = db.connect()
    maps, moves, plays = load(conn)
    tracking_start = db.get_meta(conn, "tracking_start", "")
    variants = make_variants(typical_moves(maps, moves))
    print(f"{len(plays)} parties, {len(moves)} maps mesurées, notées après les {WARMUP} premières.\n")
    print(f"{'variante':34} {'Brier/comp':>10} {'logloss/comp':>12} {'Brier/partie':>12} {'logloss/partie':>14}")
    for name, fn in variants.items():
        res = evaluate(fn, maps, moves, plays, tracking_start)
        print(f"{name:34} {res['brier_comp']:10.4f} {res['logloss_comp']:12.4f} {res['brier_partie']:12.4f} "
              f"{res['logloss_partie']:14.4f}   (n={res['n']})")


if __name__ == "__main__":
    main()
