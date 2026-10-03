"""Attributs des maps en cache et constitution d'un réservoir de maps à proposer."""
import gzip
import json
from pathlib import Path

from . import db
from . import mods as modlib
from .osufile import download_many
from .skills import CLASSIFIER_VERSION, classify, expected_ar, expected_od, map_attributes, primary

PLAYABLE_STATUSES = ("ranked", "approved", "loved")
BUNDLED_POOL = Path(__file__).with_name("pool.json.gz")
BUNDLED_RANGE = (1.0, 8.0)  # tranche d'étoiles couverte par le catalogue (voir tools/build_pool.py)


def _store(conn, beatmap_id: int, key: str, attrs: dict) -> dict:
    weights = classify(attrs)
    conn.execute(
        "INSERT OR REPLACE INTO map_attrs (beatmap_id, mods_key, stars, primary_cat, attrs, weights) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (beatmap_id, key, attrs["stars"], primary(weights), json.dumps(attrs), json.dumps(weights)),
    )
    return {"stars": attrs["stars"], "attrs": attrs, "weights": weights}


def ensure_attrs_many(conn, items: list[tuple[int, list[dict]]], log=print) -> int:
    """Calcule les attributs manquants pour une liste de (map, mods). Renvoie le nombre calculé."""
    known = {(r["beatmap_id"], r["mods_key"]) for r in conn.execute("SELECT beatmap_id, mods_key FROM map_attrs")}
    todo = {}
    for beatmap_id, mods in items:
        key = modlib.mods_key(mods)
        if (beatmap_id, key) not in known:
            todo[(beatmap_id, key)] = mods
    if not todo:
        return 0

    ids = {b for b, _ in todo}
    last = [0]

    def progress(done: int, total: int) -> None:
        if done == total or done - last[0] >= 100:
            last[0] = done
            log(f"  Téléchargement des maps : {done}/{total}")

    failed = download_many(ids, progress)
    for (beatmap_id, key), mods in todo.items():
        if beatmap_id in failed:
            continue
        try:
            _store(conn, beatmap_id, key, map_attributes(beatmap_id, mods))
        except Exception as e:  # map supprimée, fichier corrompu...
            log(f"  map {beatmap_id} ignorée : {e}")
    conn.commit()
    return len(todo) - len(failed)


def backfill_hp(conn, log=print) -> int:
    """Ajoute le HP aux maps analysées avant son suivi, depuis les fichiers .osu déjà en cache."""
    import re

    import rosu_pp_py as rosu

    from .osufile import osu_path
    rows = conn.execute("SELECT beatmap_id, mods_key, attrs FROM map_attrs").fetchall()
    updated = 0
    for r in rows:
        attrs = json.loads(r["attrs"])
        if "hp" in attrs:
            continue
        path = osu_path(r["beatmap_id"])
        if not path.exists():
            continue
        try:
            content = path.read_bytes()
            if r["mods_key"] == "":
                m = re.search(rb"^HPDrainRate:\s*([\d.]+)", content, re.M)
                hp = float(m.group(1)) if m else None
            else:
                beatmap = rosu.Beatmap(bytes=content)
                hp = rosu.BeatmapAttributesBuilder(map=beatmap, mods=json.loads(r["mods_key"])).build().hp
        except Exception:
            continue
        if hp is None:
            continue
        attrs["hp"] = round(hp, 2)
        conn.execute("UPDATE map_attrs SET attrs = ? WHERE beatmap_id = ? AND mods_key = ?",
                     (json.dumps(attrs), r["beatmap_id"], r["mods_key"]))
        updated += 1
    if updated:
        conn.commit()
        log(f"Jauge de vie (HP) ajoutée à {updated} maps.")
    return updated


def reclassify_if_needed(conn) -> bool:
    """Recalcule les poids par compétence si les règles de classement ont changé."""
    if db.get_meta(conn, "classifier_version") == CLASSIFIER_VERSION:
        return False
    rows = conn.execute("SELECT beatmap_id, mods_key, attrs FROM map_attrs").fetchall()
    for r in rows:
        weights = classify(json.loads(r["attrs"]))
        conn.execute(
            "UPDATE map_attrs SET weights = ?, primary_cat = ? WHERE beatmap_id = ? AND mods_key = ?",
            (json.dumps(weights), primary(weights), r["beatmap_id"], r["mods_key"]),
        )
    db.set_meta(conn, "classifier_version", CLASSIFIER_VERSION)
    conn.commit()
    return True


def load_bundled_pool(conn, log=print) -> None:
    """Charge le catalogue de maps déjà analysées fourni avec le projet (une seule fois par version)."""
    if not BUNDLED_POOL.exists():
        return
    with gzip.open(BUNDLED_POOL, "rt", encoding="utf-8") as f:
        data = json.load(f)
    if db.get_meta(conn, "bundled_pool") == data["version"]:
        return
    known = {r["beatmap_id"] for r in conn.execute("SELECT beatmap_id FROM map_attrs WHERE mods_key = ''")}
    for m in data["maps"]:
        db.upsert_beatmap(
            conn,
            {"id": m["id"], "beatmapset_id": m["set"], "version": m["version"], "status": m["status"],
             "playcount": m["playcount"]},
            {"artist": m["artist"], "title": m["title"], "creator": m["creator"],
             "favourite_count": m["favourites"]},
        )
        if m["id"] not in known:
            _store(conn, m["id"], "", m["attrs"])
    db.set_meta(conn, "bundled_pool", data["version"])
    conn.commit()
    log(f"Catalogue chargé : {len(data['maps'])} maps déjà analysées.")


def pool_query(category: str, lo: float, hi: float) -> str:
    """Filtre de recherche osu! adapté à la compétence (pré-tri avant le calcul local)."""
    q = f"stars>={lo - 0.05:.2f} stars<{hi + 0.05:.2f}"
    if category == "stamina":
        q += " length>=150"
    elif category == "accuracy":
        q += f" od>={expected_od(lo) + 0.5:.1f}"
    elif category == "reading":
        q += f" ar<{expected_ar(lo) - 0.5:.1f}"
    elif category == "speed":
        q += " bpm>=150"
    return q


MAX_PAGES = 4


def search_page(api, query: str, cursor: str | None, lo: float, hi: float) -> tuple[list, str]:
    """Une page de recherche : renvoie [(beatmap, beatmapset)] dans la tranche d'étoiles, et le curseur suivant."""
    result = api.search_beatmapsets(query, cursor)
    found = [
        (bmap, bset)
        for bset in result.get("beatmapsets", [])
        for bmap in bset.get("beatmaps", [])
        if bmap.get("mode") == "osu" and lo - 0.1 <= bmap["difficulty_rating"] < hi + 0.1
    ]
    return found, result.get("cursor_string") or ""


def fetch_pool(conn, api, category: str, lo: float, hi: float, log=print) -> int | None:
    """Analyse la page suivante de maps populaires pour cette recherche.

    Renvoie le nombre de maps ajoutées, ou None s'il n'y a plus de page à chercher.
    """
    query = pool_query(category, lo, hi)
    row = conn.execute("SELECT pages, cursor FROM pool_queries WHERE query = ?", (query,)).fetchone()
    if row and (row["pages"] >= MAX_PAGES or not row["cursor"]):
        return None
    pages, cursor = (row["pages"], row["cursor"]) if row else (0, None)
    log(f"Recherche de maps ({query}, page {pages + 1})…")
    found, next_cursor = search_page(api, query, cursor, lo, hi)
    for bmap, bset in found:
        db.upsert_beatmap(conn, bmap, bset)
    added = ensure_attrs_many(conn, [(bmap["id"], []) for bmap, _ in found], log)
    conn.execute(
        "INSERT OR REPLACE INTO pool_queries VALUES (?, ?, ?, ?)",
        (query, pages + 1, next_cursor, db.now_iso()),
    )
    conn.commit()
    return added
