"""Construit osuprog/pool.json.gz : TOUTES les maps osu! standard classées (ranked, approved, loved), déjà analysées.

Usage (nécessite une clé API dans .env) :
    python tools/build_pool.py <archive ou dossier>

<archive> : l'archive mensuelle officielle de toutes les maps classées, AAAA_MM_01_osu_files.tar.bz2 sur
https://data.ppy.sh (≈ 1,4 Go ; lue à la volée, rien n'est décompressé sur le disque), ou un dossier de .osu.
Les noms, statuts et popularités viennent de l'API osu! (50 maps par requête).
Pour chaque map : attributs habituels (skills.attributes_from_content), étoiles officielles et pp d'un full
combo à plusieurs précisions (calcul officiel d'osu!, voir osuprog/ppcalc.py).
"""
import gzip
import json
import re
import sys
import tarfile
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from osuprog import ppcalc  # noqa: E402
from osuprog.maps import BUNDLED_POOL, BUNDLED_RANGE, PLAYABLE_STATUSES, rosu_version  # noqa: E402
from osuprog.skills import attributes_from_content  # noqa: E402

WORKERS = 6
MODE_RE = re.compile(rb"^Mode:\s*(\d)", re.M)


def osu_files(source: Path):
    """(beatmap_id, contenu) de chaque map osu! standard de l'archive ou du dossier."""
    if source.is_dir():
        for path in source.glob("*.osu"):
            if path.stem.isdigit():
                yield int(path.stem), path.read_bytes()
        return
    with tarfile.open(source, "r|bz2") as tar:
        for member in tar:
            name = Path(member.name)
            if member.isfile() and name.suffix == ".osu" and name.stem.isdigit():
                yield int(name.stem), tar.extractfile(member).read()


def is_standard(content: bytes) -> bool:
    mode = MODE_RE.search(content[:4096])
    return mode is None or mode.group(1) == b"0"


def fetch_metadata(api, ids: list[int], cache: Path) -> dict[int, dict]:
    """Statut, noms et popularité, 50 maps par requête. Lent exprès : osu! limite cette route plus que les autres.
    Les réponses sont gardées dans `cache` : une construction interrompue reprend où elle en était."""
    known = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    todo = [b for b in ids if str(b) not in known]
    delay = 0.5
    for n, i in enumerate(range(0, len(todo), 50), 1):
        batch = todo[i:i + 50]
        while True:
            try:
                data = api.get("/beatmaps", [("ids[]", b) for b in batch]) or {}
                break
            except RuntimeError:  # trop de requêtes : on attend et on ralentit
                delay = min(delay * 2, 8)
                print(f"\n  l'API demande de ralentir : pause d'une minute (puis {delay:g} s entre requêtes)")
                time.sleep(60)
        found = {b["id"]: b for b in data.get("beatmaps", [])}
        for b in batch:  # les maps absentes de la réponse (supprimées…) sont notées pour ne pas les redemander
            bm = found.get(b)
            bset = (bm or {}).get("beatmapset") or {}
            known[str(b)] = None if bm is None else {
                "mode": bm.get("mode"), "status": bm.get("status"), "version": bm.get("version", ""),
                "beatmapset_id": bm.get("beatmapset_id"), "playcount": bm.get("playcount") or 0,
                "artist": bset.get("artist", ""), "title": bset.get("title", ""), "creator": bset.get("creator", ""),
                "favourites": bset.get("favourite_count") or 0}
        if n % 20 == 0 or i + 50 >= len(todo):
            cache.write_text(json.dumps(known), encoding="utf-8")
            print(f"  métadonnées {min(i + 50, len(todo))}/{len(todo)}", end="\r")
        time.sleep(delay)
    print()
    return {int(k): v for k, v in known.items() if v and v["mode"] == "osu" and v["status"] in PLAYABLE_STATUSES}


def analyse(job: tuple[int, str]) -> tuple[int, dict | None, str | None]:
    """Dans un processus séparé (chacun a son calculateur) : attributs + pp d'un full combo."""
    beatmap_id, path = job
    try:
        attrs = attributes_from_content(Path(path).read_bytes(), [], path)
        if not BUNDLED_RANGE[0] <= attrs["stars"] <= BUNDLED_RANGE[1]:
            return beatmap_id, None, None
        if "pp_fc" not in attrs:
            return beatmap_id, None, "calcul officiel indisponible"
        return beatmap_id, attrs, None
    except Exception as e:  # map corrompue, format inattendu...
        return beatmap_id, None, str(e)


def main() -> None:
    from osuprog.api import OsuApi

    if len(sys.argv) != 2:
        sys.exit(__doc__)
    source = Path(sys.argv[1])
    # Les processus de calcul lisent des fichiers : une archive est d'abord copiée (maps standard seulement).
    work = source if source.is_dir() else Path.home() / "osu-pp-build" / "pool-files"
    work.mkdir(parents=True, exist_ok=True)
    if ppcalc.engine_name() != ppcalc.LIVE:
        sys.exit("Le calculateur officiel (tools/ppcalc + .NET 8) est nécessaire pour construire le catalogue.")

    started = time.monotonic()
    if source.is_dir():  # le mode (standard ou non) est vérifié par l'API : inutile de relire chaque fichier
        ids = sorted(int(p.stem) for p in source.glob("*.osu") if p.stem.isdigit())
    else:
        ids = []
        for beatmap_id, content in osu_files(source):
            if is_standard(content):
                path = work / f"{beatmap_id}.osu"
                if not path.exists():
                    path.write_bytes(content)
                ids.append(beatmap_id)
                if len(ids) % 10000 == 0:
                    print(f"  {len(ids)} maps standard lues", end="\r")
    print(f"{len(ids)} maps dans {source.name}")

    meta = fetch_metadata(OsuApi(), ids, work.parent / f"{work.name}-metadata.json")
    print(f"{len(meta)} classées (ranked, approved, loved) selon l'API")

    jobs = [(bid, str(work / f"{bid}.osu")) for bid in ids if bid in meta]
    out, errors = [], 0
    with ProcessPoolExecutor(WORKERS) as pool:
        for done, (bid, attrs, error) in enumerate(pool.map(analyse, jobs, chunksize=64), 1):
            if done % 2000 == 0 or done == len(jobs):
                print(f"  analyse {done}/{len(jobs)} ({time.monotonic() - started:.0f} s)", end="\r")
            if error:
                errors += 1
            if attrs is None:
                continue
            m = meta[bid]
            out.append({
                "id": bid, "set": m["beatmapset_id"], "artist": m["artist"], "title": m["title"],
                "version": m["version"], "creator": m["creator"], "status": m["status"],
                "playcount": m["playcount"], "favourites": m["favourites"], "attrs": attrs,
            })
    print(f"\n{errors} maps illisibles ignorées")
    out.sort(key=lambda m: m["id"])
    with gzip.open(BUNDLED_POOL, "wt", encoding="utf-8") as f:
        json.dump({"version": datetime.now().strftime("%Y-%m-%d %H:%M"), "rosu": rosu_version(), "engine": ppcalc.engine_version(),
                   "fc_accuracies": ppcalc.FC_ACCURACIES, "maps": out}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{len(out)} maps écrites dans {BUNDLED_POOL} ({BUNDLED_POOL.stat().st_size // 1024} Ko) "
          f"en {time.monotonic() - started:.0f} s")


if __name__ == "__main__":
    main()
