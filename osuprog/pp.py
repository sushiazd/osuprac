"""Tes pp : détail par compétence comme sur pp.huismetbenen.nl, pp que rapporterait une map, et maps pour
atteindre un objectif de pp. Les calculs viennent de ppcalc (le code officiel d'osu! quand .NET est là)."""
import json
import math
import statistics

from . import db, model, ppcalc
from .progression import ignored_maps
from .rating import HP_REF

PP_STATUSES = ("ranked", "approved")  # les seules maps qui rapportent des pp
DEFAULT_MISS_RATE = 0.03  # miss par note quand on manque de parties pour l'estimer
HUNT_MIN_PLAYCOUNT = 5_000  # maps de la chasse aux pp : un minimum éprouvées


# --- Tes parties ----------------------------------------------------------------------------------

def refresh_score_pp(conn, log=print) -> int:
    """Calcule le détail des pp de tes parties classées (une seule fois, refait si le calcul d'osu! change ou si
    les statistiques complètes arrivent). Renvoie le nombre de parties calculées."""
    engine = ppcalc.engine_version()
    rows = conn.execute(
        f"""SELECT s.*, p.engine AS done_engine, p.exact AS done_exact FROM scores s
            JOIN beatmaps b ON b.beatmap_id = s.beatmap_id
            LEFT JOIN score_pp p ON p.score_id = s.score_id
            WHERE s.passed = 1 AND s.score_id > 0 AND s.pp IS NOT NULL AND s.source != 'inferred_fail'
              AND b.status IN ({",".join("?" * len(PP_STATUSES))})
              AND (p.score_id IS NULL OR p.engine != ? OR (p.exact = 0 AND s.stats IS NOT NULL))""",
        (*PP_STATUSES, engine),
    ).fetchall()
    done = 0
    for r in rows:
        try:
            res = ppcalc.calculate(r["beatmap_id"], json.loads(r["mods"]), [ppcalc.score_request(r)])
        except Exception:  # map introuvable, fichier illisible : on réessaiera à la prochaine synchro
            continue
        s = res["scores"][0]
        exact = res["engine"] == ppcalc.LIVE and r["stats"] is not None
        scale = 1.0
        if not exact and r["pp"] and s["pp"] > 0:
            # Sans les fins de sliders on surestime un peu : on cale le détail sur le total officiel d'osu!.
            scale = r["pp"] / s["pp"]
        conn.execute(
            "INSERT OR REPLACE INTO score_pp VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (r["score_id"], engine, s["pp"] * scale, *(s[k] * scale for k in ppcalc.SKILLS), int(exact)),
        )
        done += 1
    if done:
        conn.commit()
        log(f"pp détaillés de {done} partie{'s' if done > 1 else ''}.")
    return done


def best_scores(conn) -> list[dict]:
    """Ta meilleure partie sur chaque map classée, de la plus rentable à la moins rentable.

    Seules comptent les parties auxquelles osu! a donné des pp (pas de relax, de réglages de mods non classés,
    ni de parties restées sur ce PC)."""
    rows = conn.execute(
        """SELECT s.score_id, s.beatmap_id, s.mods, s.accuracy, s.max_combo, s.n_miss, s.ended_at, s.pp,
                  p.aim, p.speed, p.accuracy AS acc_pp, p.reading, p.flashlight,
                  b.artist, b.title, b.version, b.beatmapset_id, a.stars
           FROM scores s JOIN score_pp p ON p.score_id = s.score_id
           JOIN beatmaps b ON b.beatmap_id = s.beatmap_id
           LEFT JOIN map_attrs a ON a.beatmap_id = s.beatmap_id AND a.mods_key = s.mods_key
           WHERE s.pp IS NOT NULL"""
    ).fetchall()
    best = {}
    for r in rows:
        if r["beatmap_id"] not in best or (r["pp"] or 0) > (best[r["beatmap_id"]]["pp"] or 0):
            best[r["beatmap_id"]] = dict(r)
    return sorted(best.values(), key=lambda r: r["pp"] or 0, reverse=True)


def profile(conn) -> dict | None:
    """Total et détail par compétence.

    Le total est celui de ton profil osu! (son bonus compte plus de parties qu'on ne peut en retrouver). Le détail
    suit pp.huismetbenen.nl : chaque compétence trie tes parties selon ses propres pp, puis ×0,95 à chaque rang.
    """
    scores = best_scores(conn)
    if not scores:
        return None
    weighted = ppcalc.weighted([s["pp"] for s in scores])
    skills = {k: ppcalc.weighted(sorted((s["acc_pp" if k == "accuracy" else k] or 0 for s in scores), reverse=True))
              for k in ppcalc.SKILLS}
    official = (db.get_meta(conn, "user") or {}).get("pp")
    total = official if official and official >= weighted else weighted + ppcalc.bonus_pp(len(scores))
    return {"total": total, "weighted": weighted, "bonus": total - weighted, "skills": skills, "scores": scores,
            "official": official, "top": scores[0]["pp"]}


def gain_if(profile_: dict, new_pp: float) -> float:
    """Combien ton total gagnerait avec une nouvelle partie à new_pp, sur une map pas encore dans ton top
    (pp pondérés + un peu de bonus pour une map de plus)."""
    old = [s["pp"] for s in profile_["scores"]]
    n = len(old)
    return (ppcalc.weighted(sorted(old + [new_pp], reverse=True)) - profile_["weighted"]
            + ppcalc.bonus_pp(n + 1) - ppcalc.bonus_pp(n))


def typical_accuracy(conn) -> float:
    """Ta précision habituelle sur tes meilleures parties (médiane des 20 plus rentables), entre 0 et 1."""
    accs = [s["accuracy"] for s in best_scores(conn)[:20]]
    return round(statistics.median(accs) / 100, 3) if accs else 0.9


# --- pp d'une map -----------------------------------------------------------------------------------

def fc_pp(attrs: dict, accuracy: float) -> float | None:
    """pp d'un full combo à cette précision, interpolé depuis le catalogue (None si la map n'y est pas)."""
    values = attrs.get("pp_fc")
    if not values:
        return None
    a = min(max(accuracy, ppcalc.FC_ACCURACIES[0]), ppcalc.FC_ACCURACIES[-1])
    for i in range(len(ppcalc.FC_ACCURACIES) - 1):
        lo, hi = ppcalc.FC_ACCURACIES[i], ppcalc.FC_ACCURACIES[i + 1]
        if a <= hi:
            t = (a - lo) / (hi - lo)
            return values[i] + t * (values[i + 1] - values[i])
    return values[-1]


def map_pp(beatmap_id: int, mods: list[dict], accuracy: float, misses=(0, 1, 3)) -> dict:
    """pp exacts d'une map à une précision donnée, pour plusieurs nombres de miss."""
    res = ppcalc.calculate(beatmap_id, mods, [{"acc": accuracy, "miss": m} for m in misses])
    return {"engine": res["engine"], "difficulty": res["difficulty"],
            "by_miss": {m: s for m, s in zip(misses, res["scores"])}}


# --- Objectif pp --------------------------------------------------------------------------------------

def miss_model(conn) -> tuple[float, float]:
    """Miss par note selon les étoiles, d'après tes parties finies : taux = exp(a + b × étoiles)."""
    rows = conn.execute(
        """SELECT a.stars, s.n_miss, a.attrs FROM scores s
           JOIN map_attrs a ON a.beatmap_id = s.beatmap_id AND a.mods_key = s.mods_key
           WHERE s.passed = 1 AND s.countable = 1""").fetchall()
    bins = {}
    for r in rows:
        n = json.loads(r["attrs"]).get("n_objects") or 0
        if n:
            b = bins.setdefault(round(r["stars"] * 4) / 4, [0, 0])
            b[0] += r["n_miss"]
            b[1] += n
    pts = [(s, math.log(max(m, 0.5) / n), n) for s, (m, n) in bins.items() if n >= 200]
    if len(pts) < 3:
        return math.log(DEFAULT_MISS_RATE) - 0.5 * 3, 0.5
    w = sum(p[2] for p in pts)
    mx = sum(p[0] * p[2] for p in pts) / w
    my = sum(p[1] * p[2] for p in pts) / w
    var = sum(p[2] * (p[0] - mx) ** 2 for p in pts)
    b = sum(p[2] * (p[0] - mx) * (p[1] - my) for p in pts) / var if var else 0.5
    b = min(max(b, 0.2), 2.0)  # garde-fou : plus dur = plus de miss, sans extrapoler n'importe comment
    return my - b * mx, b


def expected_misses(model_: tuple[float, float], stars: float, n_objects: int) -> float:
    a, b = model_
    return math.exp(a + b * stars) * n_objects


def hunt(conn, target: float, accuracy: float, limit: int = 8) -> list[dict]:
    """Maps où un full combo à ta précision rapporte au moins `target` pp, les plus accessibles d'abord :
    peu de miss attendus (courte, à ta portée), assez de chances de la finir, et pas trop au-dessus du but."""
    clear = db.get_meta(conn, "clear_level")
    mm = miss_model(conn)
    ignored = ignored_maps(conn)
    already = {s["beatmap_id"]: s["pp"] for s in best_scores(conn)}
    rows = conn.execute(
        f"""SELECT a.beatmap_id, a.stars, a.attrs, b.artist, b.title, b.version, b.beatmapset_id, b.playcount,
                   b.favourites
            FROM map_attrs a JOIN beatmaps b ON b.beatmap_id = a.beatmap_id
            WHERE a.mods_key = '' AND b.status IN ({",".join("?" * len(PP_STATUSES))}) AND a.attrs LIKE '%pp_fc%'
              AND (b.playcount >= ? OR b.favourites >= 50)""",
        (*PP_STATUSES, HUNT_MIN_PLAYCOUNT),
    ).fetchall()
    out = []
    for r in rows:
        if r["beatmap_id"] in ignored or already.get(r["beatmap_id"], 0) >= target:
            continue
        attrs = json.loads(r["attrs"])
        pp = fc_pp(attrs, accuracy)
        if pp is None or pp < target or pp > target * 1.35:
            continue
        n = attrs.get("n_objects") or 0
        misses = expected_misses(mm, r["stars"], n)
        p_finish = model.finish_prob_for(clear, r["stars"], attrs.get("hp", HP_REF))
        # Moins de miss attendus d'abord ; une map dure à finir (HP, longueur) recule ; légère préférence pour
        # les maps populaires (mieux construites).
        score = misses / max(p_finish or 0.5, 0.05) / model.quality(r["playcount"], r["favourites"])
        out.append({"beatmap_id": r["beatmap_id"], "stars": r["stars"], "pp": pp, "misses": misses,
                    "p_finish": p_finish, "length_s": attrs.get("length_s"), "hp": attrs.get("hp"),
                    "n_objects": n, "title": r["title"], "artist": r["artist"], "version": r["version"],
                    "set_id": r["beatmapset_id"], "attrs": attrs, "rank": score})
    out.sort(key=lambda m: m["rank"])
    return out[:limit]


# --- Calculateur ---------------------------------------------------------------------------------------

def search_maps(conn, text: str, limit: int = 30) -> list[dict]:
    """Maps du catalogue dont le titre, l'artiste, la difficulté ou le mappeur contient chaque mot tapé."""
    words = [w for w in text.lower().split() if w]
    if not words:
        return []
    where = " AND ".join(["lower(b.artist || ' ' || b.title || ' ' || b.version || ' ' || b.creator) LIKE ?"] * len(words))
    rows = conn.execute(
        f"""SELECT b.beatmap_id, b.artist, b.title, b.version, b.creator, a.stars
            FROM beatmaps b LEFT JOIN map_attrs a ON a.beatmap_id = b.beatmap_id AND a.mods_key = ''
            WHERE {where} ORDER BY b.playcount DESC LIMIT ?""",
        (*[f"%{w}%" for w in words], limit),
    ).fetchall()
    return [dict(r) for r in rows]


def parse_beatmap_id(text: str) -> int | None:
    """« 545933 », « https://osu.ppy.sh/beatmaps/545933 » ou « …/beatmapsets/123#osu/545933 » -> 545933."""
    import re
    text = text.strip()
    if text.isdigit():
        return int(text)
    m = re.search(r"(?:#osu/|/beatmaps/|/b/)(\d+)", text)
    return int(m.group(1)) if m else None
