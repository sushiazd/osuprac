"""Construit osuprog/pool.json.gz : un catalogue de maps populaires déjà analysées.

Usage (nécessite une clé API dans .env) : python tools/build_pool.py
Le catalogue évite à chaque joueur de télécharger des centaines de maps
à sa première synchronisation.
"""
import gzip
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from osuprog.api import OsuApi  # noqa: E402
from osuprog.maps import BUNDLED_POOL, pool_query, rosu_version, search_page  # noqa: E402
from osuprog.osufile import download_many  # noqa: E402
from osuprog.skills import map_attributes  # noqa: E402

BANDS = [round(1.0 + 0.5 * i, 1) for i in range(14)]  # 1,0★ à 8,0★
QUERIES = {"aim": 2, "speed": 1, "stamina": 1, "accuracy": 1, "reading": 1}  # catégorie -> pages


def main() -> None:
    api = OsuApi()
    jobs = []
    for lo in BANDS:
        for cat, pages in QUERIES.items():
            jobs.append((pool_query(cat, lo, lo + 0.5), pages, lo))

    def run(job):
        query, pages, lo = job
        found, cursor = [], None
        for _ in range(pages):
            page, cursor = search_page(api, query, cursor, lo, lo + 0.5)
            found += page
            if not cursor:
                break
        return found

    maps = {}
    for found in api.map_parallel(run, jobs, lambda d, t: print(f"recherches {d}/{t}", end="\r")):
        for bmap, bset in found:
            maps[bmap["id"]] = (bmap, bset)
    print(f"\n{len(maps)} maps trouvées, téléchargement…")
    failed = download_many(maps, lambda d, t: print(f"  {d}/{t}", end="\r") if d % 50 == 0 or d == t else None)

    out = []
    for bid, (bmap, bset) in maps.items():
        if bid in failed:
            continue
        try:
            attrs = map_attributes(bid, [])
        except Exception as e:
            print(f"\nmap {bid} ignorée : {e}")
            continue
        out.append({
            "id": bid, "set": bset["id"], "artist": bset["artist"], "title": bset["title"],
            "version": bmap["version"], "creator": bset["creator"], "status": bmap["status"],
            "playcount": bmap.get("playcount") or 0, "favourites": bset.get("favourite_count") or 0,
            "attrs": attrs,
        })
    with gzip.open(BUNDLED_POOL, "wt", encoding="utf-8") as f:
        json.dump({"version": date.today().isoformat(), "rosu": rosu_version(), "maps": out}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\n{len(out)} maps écrites dans {BUNDLED_POOL} ({BUNDLED_POOL.stat().st_size // 1024} Ko)")


if __name__ == "__main__":
    main()
