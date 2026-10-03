"""Paliers par compétence, examen de passage de rang et séance du jour.

Les niveaux sont stockés en centièmes d'étoile (425 = 4,25★) pour éviter
les problèmes d'arrondi.
"""
import json
import math
import sqlite3
from pathlib import Path
from datetime import datetime

from . import db, model
from .maps import BUNDLED_RANGE, PLAYABLE_STATUSES, fetch_pool
from .mods import EASY_MODS
from .rating import DEFAULT_OBJECTS, HP_REF, effective_stars
from .skills import CATEGORIES, CRITERIA, CRITERIA_VERSION, EXAM_CRITERION, Criterion, meets, outcome

STEP_C = 25            # un palier tous les 0,25★
RANK_STEP_C = 50       # un rang tous les 0,5★
MAPS_PER_TIER = 3
EXAM_MAPS = 2
MIN_WEIGHT = 0.4       # part minimale de la compétence dans une map de palier
FINISH_FLOOR = 0.30    # on ne propose pas en palier une map que tu as très peu de chances de finir
STUCK_ATTEMPTS = 8     # essais sans atteindre l'objectif avant de remplacer une map de palier
UNFINISHABLE_ATTEMPTS = 3  # essais sans jamais la finir (et peu de chances d'y arriver) avant remplacement
PRIOR_STRENGTH = 3     # poids de l'estimation du modèle face à tes essais réels (en nombre d'essais)
AUTO_SKIP_MARGIN_C = 50  # note Elo ≥ palier + 0,5★…
AUTO_SKIP_GAMES = 8      # …avec assez de parties : palier validé d'office


def floor_level(stars: float) -> int:
    return int(math.floor(stars * 100 / STEP_C)) * STEP_C


def fmt(level_c: int) -> str:
    return f"{level_c / 100:.2f}".replace(".", ",") + "★"


def criterion_for(category: str) -> Criterion:
    return EXAM_CRITERION if category == "exam" else CRITERIA[category]


# --- Résultats du joueur sur une map ----------------------------------------------------

def n_objects_of(conn, beatmap_id: int) -> int:
    row = conn.execute("SELECT attrs FROM map_attrs WHERE beatmap_id = ? LIMIT 1", (beatmap_id,)).fetchone()
    return (json.loads(row["attrs"]).get("n_objects") if row else None) or DEFAULT_OBJECTS


def best_result(conn, beatmap_id: int, crit: Criterion) -> dict | None:
    """Meilleure tentative sur cette map (sans mod facilitant) au regard du critère."""
    n_obj = n_objects_of(conn, beatmap_id)
    rows = conn.execute(
        "SELECT accuracy, n_miss, passed, mods, ended_at FROM scores "
        "WHERE beatmap_id = ? AND countable = 1",
        (beatmap_id,),
    ).fetchall()
    best = None
    for r in rows:
        acr = {m["acronym"] for m in json.loads(r["mods"])}
        if acr & EASY_MODS:
            continue
        res = {
            "met": meets(bool(r["passed"]), r["accuracy"], r["n_miss"], n_obj, crit),
            "score": outcome(bool(r["passed"]), r["accuracy"], r["n_miss"], n_obj, crit),
            "accuracy": r["accuracy"],
            "misses": r["n_miss"],
            "passed": bool(r["passed"]),
            "attempts": len(rows),
        }
        if best is None or (res["met"], res["score"], res["accuracy"]) > (best["met"], best["score"], best["accuracy"]):
            best = res
    return best


# --- Choix des maps ---------------------------------------------------------------------

def candidates(conn, category: str | None, lo_c: int, hi_c: int, min_weight: float) -> list[dict]:
    """Maps du réservoir dans la tranche d'étoiles, de la meilleure à la moins bonne pour ce palier.

    Une bonne map de palier est : pure dans la compétence, populaire (donc bien construite), de longueur
    raisonnable, et à ta portée (≈ 55 % de chances d'atteindre l'objectif, pour que les 3 soient jouables).
    category=None cherche des maps polyvalentes (aucune compétence dominante), pour l'examen.
    """
    rows = conn.execute(
        f"""SELECT a.beatmap_id, a.stars, a.weights, a.attrs, b.beatmapset_id, b.playcount, b.favourites
            FROM map_attrs a JOIN beatmaps b ON b.beatmap_id = a.beatmap_id
            WHERE a.mods_key = '' AND a.stars >= ? AND a.stars < ?
              AND b.status IN ({",".join("?" * len(PLAYABLE_STATUSES))})""",
        (lo_c / 100, hi_c / 100, *PLAYABLE_STATUSES),
    ).fetchall()
    ratings = {r["category"]: r["rating"] for r in conn.execute("SELECT category, rating FROM ratings")}
    calib = model.load_calibration(conn)
    clear = db.get_meta(conn, "clear_level")
    out = []
    for r in rows:
        w = json.loads(r["weights"])
        attrs = json.loads(r["attrs"])
        hp = attrs.get("hp", HP_REF)
        if clear and model.finish_prob(r["stars"], hp, clear["level"], clear.get("scale", 0.55),
                                       clear.get("hp_k", 0.0)) < FINISH_FLOOR:
            continue
        if category is None:
            if max(w.values()) > 0.5:
                continue
            fit = 1 - max(w.values())
        else:
            fit = w.get(category, 0)
            if fit < min_weight:
                continue
        length = attrs.get("length_s") or 0
        reach = (model.bump(model.p_map(w, ratings, effective_stars(r["stars"], hp), calib), 0.55, 0.3)
                 if len(ratings) == len(CATEGORIES) else 1.0)
        out.append({
            "beatmap_id": r["beatmap_id"],
            "beatmapset_id": r["beatmapset_id"],
            "stars": r["stars"],
            "rank": fit * model.quality(r["playcount"], r["favourites"])
                    * model.duration_factor(length, w.get("stamina", 0)) * (0.7 + 0.3 * reach),
        })
    out.sort(key=lambda c: c["rank"], reverse=True)
    return out


def used_maps(conn) -> tuple[set[int], set[int]]:
    rows = conn.execute(
        "SELECT t.beatmap_id, b.beatmapset_id FROM tier_maps t "
        "LEFT JOIN beatmaps b ON b.beatmap_id = t.beatmap_id"
    ).fetchall()
    return {r["beatmap_id"] for r in rows}, {r["beatmapset_id"] for r in rows}


def pick(conn, api, category: str | None, lo_c: int, hi_c: int, n: int,
         exclude_ids: set[int], exclude_sets: set[int], log=print) -> list[int]:
    """Choisit n maps de palier ; va chercher de nouvelles maps sur osu! si le réservoir est trop maigre.

    Les maps dont tu atteins déjà l'objectif sont écartées : elles valideraient le palier sans rien t'apprendre.
    """
    crit = criterion_for(category or "exam")

    def select(min_weight: float, widen: int) -> list[int]:
        chosen, sets = [], set(exclude_sets)
        for c in candidates(conn, category, lo_c - widen, hi_c + widen, min_weight):
            if c["beatmap_id"] in exclude_ids or c["beatmapset_id"] in sets:
                continue
            res = best_result(conn, c["beatmap_id"], crit)
            if res and res["met"]:
                continue
            chosen.append(c["beatmap_id"])
            sets.add(c["beatmapset_id"])
            if len(chosen) == n:
                break
        return chosen

    chosen = select(MIN_WEIGHT, 0)
    # Dans la plage du catalogue fourni, pas de recherche en ligne : c'est ce qui rendait la synchro lente.
    catalogued = db.get_meta(conn, "bundled_pool") and BUNDLED_RANGE[0] <= lo_c / 100 < BUNDLED_RANGE[1]
    for _ in range(3):  # jusqu'à 3 pages de recherche supplémentaires
        if len(chosen) >= n or api is None or catalogued:
            break
        if fetch_pool(conn, api, category or "aim", lo_c / 100, hi_c / 100, log=log) is None:
            break
        chosen = select(MIN_WEIGHT, 0)
    if len(chosen) < n:
        chosen = select(0.25, 10)
    if len(chosen) < n:  # compétence rare à ce niveau : les maps qui s'en rapprochent le plus
        chosen = select(0.0, 10)
    return chosen


def ensure_tier_maps(conn, api, category: str, level_c: int, log=print) -> list[int]:
    n = EXAM_MAPS if category == "exam" else MAPS_PER_TIER
    rows = conn.execute(
        "SELECT slot, beatmap_id FROM tier_maps WHERE category = ? AND level_c = ? ORDER BY slot",
        (category, level_c),
    ).fetchall()
    have = [r["beatmap_id"] for r in rows]
    if len(have) < n:
        ids, sets = used_maps(conn)
        ids |= {m["id"] for m in db.get_meta(conn, "retired_tier_maps", [])}
        new = pick(conn, api, None if category == "exam" else category,
                   level_c, level_c + STEP_C, n - len(have), ids, sets, log=log)
        next_slot = max((r["slot"] for r in rows), default=-1) + 1
        for i, bid in enumerate(new, start=next_slot):
            conn.execute("INSERT INTO tier_maps VALUES (?, ?, ?, ?)", (category, level_c, i, bid))
        conn.commit()
        have += new
        if len(have) < n:
            log(f"Pas assez de maps trouvées pour {category} {fmt(level_c)} ({len(have)}/{n}).")
    return have


def migrate_if_needed(conn, log=print) -> bool:
    """Quand les critères de réussite changent, les paliers déjà attribués n'ont plus de sens :
    on les recalcule depuis tes nouvelles notes, après une sauvegarde de la base."""
    previous = db.get_meta(conn, "criteria_version")
    if previous == CRITERIA_VERSION:
        return False
    had_progress = conn.execute("SELECT count(*) FROM progress").fetchone()[0] > 0
    if had_progress:
        backup = Path(db.DB_PATH).with_name(f"osu-avant-criteres-v{CRITERIA_VERSION}.sqlite")
        if not backup.exists():
            target = sqlite3.connect(backup)
            conn.backup(target)
            target.close()
        for table in ("progress", "tier_maps", "tier_log", "session_plan"):
            conn.execute(f"DELETE FROM {table}")
        for key in ("rank_c", "session_focus", "session_variant"):
            db.del_meta(conn, key)
        log("Les objectifs de réussite ont été recalibrés : tes paliers repartent de ton niveau réel "
            f"(ancienne base sauvegardée dans {backup.name}).")
    db.set_meta(conn, "criteria_version", CRITERIA_VERSION)
    conn.commit()
    return had_progress


def retire_stuck_tier_maps(conn, log=print) -> int:
    """Remplace les maps de palier qui te bloquent : une map que tu as très peu de chances de finir (jauge de vie
    trop dure pour ton niveau), ou sur laquelle tu t'acharnes sans atteindre l'objectif. Une map réussie
    n'est jamais retirée."""
    clear = db.get_meta(conn, "clear_level")
    retired = db.get_meta(conn, "retired_tier_maps", [])
    removed = 0
    tiers = [(cat, v + STEP_C) for cat, v in load_progress(conn).items()]
    state = exam_state(conn)
    if state["unlocked"]:
        tiers.append(("exam", state["target_c"]))
    for cat, level in tiers:
        crit = criterion_for(cat)
        for r in conn.execute("SELECT slot, beatmap_id FROM tier_maps WHERE category = ? AND level_c = ?", (cat, level)).fetchall():
            res = best_result(conn, r["beatmap_id"], crit)
            if res and res["met"]:
                continue
            plays = conn.execute(
                "SELECT passed FROM scores WHERE beatmap_id = ? AND countable = 1 AND source != 'inferred_fail'",
                (r["beatmap_id"],)).fetchall()
            attempts, finishes = len(plays), sum(bool(p["passed"]) for p in plays)
            finished = finishes > 0
            row = conn.execute("SELECT stars, attrs FROM map_attrs WHERE beatmap_id = ? AND mods_key = ''",
                               (r["beatmap_id"],)).fetchone()
            p_finish = None
            if clear and row:
                hp = json.loads(row["attrs"]).get("hp", HP_REF)
                p_finish = model.finish_prob(row["stars"], hp, clear["level"], clear.get("scale", 0.55),
                                             clear.get("hp_k", 0.0))
            reason = None
            if attempts >= STUCK_ATTEMPTS:
                reason = f"{attempts} essais sans atteindre l'objectif"
            elif attempts >= UNFINISHABLE_ATTEMPTS and not finished and p_finish is not None and (
                    (PRIOR_STRENGTH * p_finish + finishes) / (PRIOR_STRENGTH + attempts) < 0.35):
                # L'estimation du modèle est corrigée par tes essais : des fails à répétition prouvent que
                # la map est plus dure à finir que ses étoiles ne le disent (souvent la jauge de vie).
                reason = (f"jamais finie en {attempts} essais : plus dure à finir que ses étoiles ne l'indiquent "
                          "(jauge de vie)")
            elif attempts == 0 and p_finish is not None and p_finish < FINISH_FLOOR:
                reason = "très peu de chances de la finir (jauge de vie trop dure pour ton niveau)"
            if reason:
                conn.execute("DELETE FROM tier_maps WHERE category = ? AND level_c = ? AND slot = ?",
                             (cat, level, r["slot"]))
                retired.append({"id": r["beatmap_id"], "category": cat, "level_c": level, "reason": reason,
                                "attempts": attempts, "at": db.now_iso()})
                removed += 1
                log(f"Map de palier remplacée ({cat} {fmt(level)}) : {reason}.")
    if removed:
        db.set_meta(conn, "retired_tier_maps", retired[-30:])
        conn.commit()
    return removed


def refresh_open_tiers(conn) -> int:
    """Après un changement de classement : retire des paliers en cours les maps qui
    ne correspondent plus à leur compétence (sauf celles déjà réussies)."""
    removed = 0
    for cat, validated in load_progress(conn).items():
        level = validated + STEP_C
        rows = conn.execute(
            """SELECT t.slot, t.beatmap_id, a.weights FROM tier_maps t
               JOIN map_attrs a ON a.beatmap_id = t.beatmap_id AND a.mods_key = ''
               WHERE t.category = ? AND t.level_c = ?""",
            (cat, level),
        ).fetchall()
        for r in rows:
            res = best_result(conn, r["beatmap_id"], CRITERIA[cat])
            if json.loads(r["weights"]).get(cat, 0) < MIN_WEIGHT and not (res and res["met"]):
                conn.execute("DELETE FROM tier_maps WHERE category = ? AND level_c = ? AND slot = ?",
                             (cat, level, r["slot"]))
                removed += 1
    conn.commit()
    return removed


# --- Progression ------------------------------------------------------------------------

def load_progress(conn) -> dict[str, int]:
    return {r["category"]: r["validated_c"] for r in conn.execute("SELECT * FROM progress")}


def validate_tier(conn, category: str, level_c: int, how: str, log=print) -> None:
    if category != "exam":
        conn.execute(
            "INSERT OR REPLACE INTO progress VALUES (?, ?)", (category, level_c)
        )
    conn.execute(
        "INSERT OR REPLACE INTO tier_log VALUES (?, ?, ?, ?)", (category, level_c, how, db.now_iso())
    )
    conn.commit()
    log(f"Palier validé : {category} {fmt(level_c)} ({how})")


def exam_state(conn) -> dict:
    progress = load_progress(conn)
    rank_c = db.get_meta(conn, "rank_c", 0)
    target = rank_c + RANK_STEP_C
    missing = [c for c in CATEGORIES if progress.get(c, 0) < target]
    return {"rank_c": rank_c, "target_c": target, "unlocked": not missing, "missing": missing}


def update(conn, api, ratings: dict[str, dict], log=print) -> None:
    """Initialise, valide les paliers terminés, attribue les maps des paliers en cours."""
    progress = load_progress(conn)
    for cat in CATEGORIES:
        if cat not in progress:
            start = floor_level(ratings[cat]["rating"]) - STEP_C
            conn.execute("INSERT INTO progress VALUES (?, ?)", (cat, start))
            progress[cat] = start
    if db.get_meta(conn, "rank_c") is None:
        lowest = min(progress.values())
        db.set_meta(conn, "rank_c", lowest // RANK_STEP_C * RANK_STEP_C)
    conn.commit()

    for cat in CATEGORIES:
        while True:
            current = progress[cat] + STEP_C
            r = ratings[cat]
            if r["games"] >= AUTO_SKIP_GAMES and r["rating"] * 100 >= current + AUTO_SKIP_MARGIN_C:
                validate_tier(conn, cat, current, "auto : note Elo largement au-dessus", log)
            else:
                maps = ensure_tier_maps(conn, api, cat, current, log)
                results = [best_result(conn, b, CRITERIA[cat]) for b in maps]
                if len(maps) < MAPS_PER_TIER or not all(res and res["met"] for res in results):
                    break
                validate_tier(conn, cat, current, "maps réussies", log)
            progress[cat] = current

    while True:
        state = exam_state(conn)
        if not state["unlocked"]:
            break
        maps = ensure_tier_maps(conn, api, "exam", state["target_c"], log)
        results = [best_result(conn, b, EXAM_CRITERION) for b in maps]
        if len(maps) < EXAM_MAPS or not all(res and res["met"] for res in results):
            break
        validate_tier(conn, "exam", state["target_c"], "examen réussi", log)
        db.set_meta(conn, "rank_c", state["target_c"])
        conn.commit()


# --- Séance du jour ---------------------------------------------------------------------

CALIBRATED_GAMES = 3  # en dessous, la note d'une compétence est encore trop incertaine


def focus_skills(ratings: dict[str, dict]) -> tuple[str, str, str]:
    """(plus faible, deuxième plus faible, plus forte) parmi les compétences assez mesurées."""
    measured = [c for c in CATEGORIES if ratings[c]["games"] >= CALIBRATED_GAMES]
    pool = measured if len(measured) >= 3 else CATEGORIES
    order = sorted(pool, key=lambda c: ratings[c]["rating"])
    return order[0], order[1], order[-1]


def played_on(conn, beatmap_id: int, day: str) -> list[dict]:
    """Parties jouées sur cette map ce jour-là (heure locale), de la plus récente à la plus ancienne."""
    rows = conn.execute(
        "SELECT accuracy, n_miss, passed, mods, ended_at FROM scores "
        "WHERE beatmap_id = ? AND countable = 1 AND source != 'inferred_fail' ORDER BY ended_at DESC",
        (beatmap_id,),
    ).fetchall()
    out = []
    for r in rows:
        local_day = datetime.fromisoformat(r["ended_at"].replace("Z", "+00:00")).astimezone().date().isoformat()
        if local_day == day:
            out.append(dict(r))
    return out
