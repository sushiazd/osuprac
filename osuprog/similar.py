"""Maps qui ressemblent à une map donnée : même niveau, même format, mêmes sauts et même rythme.

Deux étapes : le catalogue donne une présélection instantanée, puis les sauts et le rythme sont mesurés dans les
fichiers .osu de cette présélection (les étoiles seules ne disent pas si deux maps se jouent pareil).
"""
import json
import math
import statistics
from datetime import datetime, timedelta, timezone

from . import osufile, pp
from .progression import MIN_FAVOURITES, MIN_PLAYCOUNT, ignored_maps
from .rating import HP_REF

STAR_WINDOW = 0.5
SHORTLIST = 40  # fichiers .osu téléchargés au plus par recherche
FEATURE_WEIGHTS = {"ratio": 2.0, "aim": 1.5, "speed": 1.0, "ar": 1.0, "cs": 1.0, "od": 0.4, "length": 0.8,
                   "density": 1.2, "stream": 1.5, "slider": 0.6, "combo": 0.6}
MOVEMENT_WEIGHTS = {"jump_vel": 2.0, "sharp_frac": 1.2, "rhythm_irregular": 1.0, "sv_changes": 0.4}
MOVEMENT_SHARE = 0.35  # poids des sauts et du rythme face aux chiffres du catalogue
MAX_STREAM_GAP = 0.10
LENGTH_RANGE = (0.6, 2.0)  # durée permise, par rapport à la map de départ
HP_MARGIN, HP_ALWAYS_OK = 1.5, 5.0
RECENT_DAYS = 30
MIN_PLAYS = 8  # parties à ce niveau pour estimer ce que rapporte un bon run
GOOD_RUN = 0.75  # « bon run » : 3e quartile de tes parties finies


def features(attrs: dict) -> dict | None:
    """Ce qu'on compare entre deux maps (None si le catalogue n'a pas ces chiffres)."""
    try:
        length = max(attrs["length_s"], 1)
        n = max(attrs["n_objects"], 1)
        speed = max(attrs["speed"], 0.01)
        return {
            "ratio": attrs["aim"] / speed, "aim": attrs["aim"], "speed": speed,
            "ar": attrs["ar"], "cs": attrs["cs"], "od": attrs["od"],
            "length": math.log(length), "density": n / length,
            "stream": attrs.get("stream_frac") or 0.0,
            "slider": attrs.get("slider_factor") or 1.0,
            "combo": (attrs.get("max_combo") or n) / n,
        }
    except (KeyError, TypeError):
        return None


def _distance(a: dict, b: dict, weights: dict, spread: dict) -> float:
    total = sum(w * ((a[k] - b[k]) / spread[k]) ** 2 for k, w in weights.items())
    return math.sqrt(total / sum(weights.values()))


def _same_format(ref: dict, attrs: dict) -> bool:
    """Écarte ce qui se joue autrement quoi qu'en disent les chiffres : longs streams, durée, drain de vie."""
    return (abs((attrs.get("stream_frac") or 0) - (ref.get("stream_frac") or 0)) <= MAX_STREAM_GAP
            and LENGTH_RANGE[0] * ref["length_s"] <= attrs["length_s"] <= LENGTH_RANGE[1] * ref["length_s"]
            and attrs.get("hp", HP_REF) <= max(ref.get("hp", HP_REF) + HP_MARGIN, HP_ALWAYS_OK))


def shortlist(ref_attrs: dict, candidates: list[dict], size: int = SHORTLIST) -> list[dict]:
    """Les candidates les plus proches sur les chiffres du catalogue, une seule difficulté par mapset."""
    ref = features(ref_attrs)
    pool = [(c, f) for c in candidates if (f := features(c["attrs"]))]
    if not ref or not pool:
        return []
    spread = {k: statistics.pstdev(f[k] for _, f in pool) or 1.0 for k in FEATURE_WEIGHTS}
    scored = sorted(((_distance(f, ref, FEATURE_WEIGHTS, spread), c) for c, f in pool), key=lambda x: x[0])
    out, seen = [], set()
    for distance, c in scored:
        if c["set_id"] in seen or not _same_format(ref_attrs, c["attrs"]):
            continue
        seen.add(c["set_id"])
        out.append({**c, "distance": distance})
        if len(out) == size:
            break
    return out


def _movement(beatmap_id: int) -> dict | None:
    try:
        return osufile.movement_features(osufile.get_osu_bytes(beatmap_id))
    except Exception:  # fichier introuvable ou illisible
        return None


def refine(ref_id: int, short: list[dict]) -> list[dict]:
    """Reclasse la présélection d'après les sauts et le rythme réels. Sans fichiers, garde l'ordre du catalogue."""
    failed = osufile.download_many([ref_id] + [c["beatmap_id"] for c in short])
    ref = None if ref_id in failed else _movement(ref_id)
    if ref is None:
        return short
    analysed = [(c, m) for c in short if c["beatmap_id"] not in failed and (m := _movement(c["beatmap_id"]))]
    if not analysed:
        return short
    spread = {k: statistics.pstdev(m[k] for _, m in analysed) or 1.0 for k in MOVEMENT_WEIGHTS}
    out = [{**c, "distance": math.hypot(c["distance"], MOVEMENT_SHARE * _distance(m, ref, MOVEMENT_WEIGHTS, spread))}
           for c, m in analysed]
    return sorted(out, key=lambda c: c["distance"])


def good_run(conn, stars: float) -> tuple[float, float] | None:
    """(précision, rendement) de tes bons runs vers ce niveau ; rendement = pp réels / pp d'un full combo à cette
    précision. Parties récentes d'abord, tout l'historique s'il y en a trop peu, None si tu n'as rien joué là."""
    recent = (datetime.now(timezone.utc) - timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for since in (recent, ""):
        rows = conn.execute(
            """SELECT s.pp, s.accuracy, a.attrs FROM scores s
               JOIN map_attrs a ON a.beatmap_id = s.beatmap_id AND a.mods_key = ''
               WHERE s.passed = 1 AND s.countable = 1 AND s.pp IS NOT NULL AND s.mods = '[]'
                 AND a.stars BETWEEN ? AND ? AND s.ended_at >= ?""",
            (stars - STAR_WINDOW, stars + STAR_WINDOW, since),
        ).fetchall()
        pairs = [(r["accuracy"], r["pp"] / fc) for r in rows
                 if (fc := pp.fc_pp(json.loads(r["attrs"]), r["accuracy"] / 100))]
        if len(pairs) >= MIN_PLAYS:
            accuracies = sorted(a for a, _ in pairs)
            yields = sorted(y for _, y in pairs)
            at = int(GOOD_RUN * (len(pairs) - 1))
            return accuracies[at], yields[at]
    return None


def find(conn, beatmap_id: int, limit: int = 10) -> dict | None:
    """Les maps classées, pas encore jouées, les plus proches de `beatmap_id`. None si le catalogue ne la connaît pas.

    Chaque map porte `pp` : ce qu'un bon run à toi rapporterait (`estimated`), ou à défaut un full combo à ta
    précision habituelle.
    """
    row = conn.execute(
        """SELECT a.stars, a.attrs, b.artist, b.title, b.version, b.beatmapset_id
           FROM map_attrs a JOIN beatmaps b ON b.beatmap_id = a.beatmap_id
           WHERE a.beatmap_id = ? AND a.mods_key = ''""", (beatmap_id,)).fetchone()
    if not row or not features(ref_attrs := json.loads(row["attrs"])):
        return None
    excluded = ignored_maps(conn) | {r[0] for r in conn.execute("SELECT DISTINCT beatmap_id FROM scores")}
    statuses = ",".join("?" * len(pp.PP_STATUSES))
    rows = conn.execute(
        f"""SELECT a.beatmap_id, a.stars, a.attrs, b.artist, b.title, b.version, b.beatmapset_id
            FROM map_attrs a JOIN beatmaps b ON b.beatmap_id = a.beatmap_id
            WHERE a.mods_key = '' AND a.stars BETWEEN ? AND ? AND b.status IN ({statuses})
              AND a.attrs LIKE '%pp_fc%' AND (b.playcount >= ? OR b.favourites >= ?) AND b.beatmapset_id != ?""",
        (row["stars"] - STAR_WINDOW, row["stars"] + STAR_WINDOW, *pp.PP_STATUSES,
         MIN_PLAYCOUNT, MIN_FAVOURITES, row["beatmapset_id"]),
    ).fetchall()
    candidates = [{"beatmap_id": r["beatmap_id"], "stars": r["stars"], "attrs": json.loads(r["attrs"]),
                   "artist": r["artist"], "title": r["title"], "version": r["version"], "set_id": r["beatmapset_id"]}
                  for r in rows if r["beatmap_id"] not in excluded]
    closest = refine(beatmap_id, shortlist(ref_attrs, candidates))[:limit]
    run = good_run(conn, row["stars"])
    accuracy = run[0] / 100 if run else pp.typical_accuracy(conn)
    gain = run[1] if run else 1.0
    maps = [{**m, "pp": fc * gain, "length_s": m["attrs"].get("length_s"), "hp": m["attrs"].get("hp")}
            for m in closest if (fc := pp.fc_pp(m["attrs"], accuracy))]
    record = conn.execute("SELECT max(pp) FROM scores WHERE beatmap_id = ?", (beatmap_id,)).fetchone()[0]
    return {"ref": {"title": row["title"], "version": row["version"], "stars": row["stars"], "best_pp": record},
            "maps": maps, "estimated": run is not None}
