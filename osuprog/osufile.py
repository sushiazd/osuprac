"""Téléchargement des fichiers .osu (cache local) et détection des motifs de streams."""
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from .config import MAPS_DIR

# Deux notes à moins de 110 ms d'écart (à vitesse normale) = 1/4 à 136 BPM ou plus.
STREAM_DT_MS = 110
MIN_DT_MS = 25
DOWNLOAD_WORKERS = 4

_local = threading.local()


def _session() -> requests.Session:
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
        _local.session.headers["User-Agent"] = "osu-progression (usage perso)"
    return _local.session


def osu_path(beatmap_id: int):
    return MAPS_DIR / f"{beatmap_id}.osu"


# Le serveur officiel ralentit fortement les téléchargements en parallèle ; ce miroir public
# sert les mêmes fichiers (vérifié octet par octet) plus vite. On reste poli : peu de
# téléchargements simultanés, et une source qui nous refuse (403/429) est laissée de côté.
SOURCES = [
    "https://osu.direct/api/osu/{id}",
    "https://osu.ppy.sh/osu/{id}",
]
_blocked_sources: set[str] = set()


def get_osu_bytes(beatmap_id: int) -> bytes:
    path = osu_path(beatmap_id)
    if path.exists() and path.stat().st_size > 0:
        return path.read_bytes()
    for attempt in range(2):
        for source in SOURCES:
            if source in _blocked_sources and source != SOURCES[-1]:
                continue
            try:
                r = _session().get(source.format(id=beatmap_id), timeout=20)
            except requests.RequestException:
                continue
            if r.status_code in (403, 429):
                _blocked_sources.add(source)
                continue
            if r.status_code == 200 and r.content.lstrip(b"\xef\xbb\xbf").startswith(b"osu file format"):
                path.write_bytes(r.content)
                return r.content
        time.sleep(1 + attempt * 2)
    raise RuntimeError(f"Impossible de télécharger la map {beatmap_id}")


def download_many(beatmap_ids, progress=None) -> set[int]:
    """Télécharge en parallèle les .osu absents du cache. Renvoie les ids en échec."""
    todo = [b for b in set(beatmap_ids) if not osu_path(b).exists()]
    failed = set()
    if not todo:
        return failed
    with ThreadPoolExecutor(DOWNLOAD_WORKERS) as pool:
        futures = {pool.submit(get_osu_bytes, b): b for b in todo}
        for done, future in enumerate(as_completed(futures), 1):
            try:
                future.result()
            except Exception:
                failed.add(futures[future])
            if progress:
                progress(done, len(todo))
    return failed


def parse_hit_objects(content: bytes) -> list[tuple[int, int]]:
    """Renvoie (temps_ms, type) pour chaque objet. type : 0 cercle, 1 slider, 3 spinner."""
    objects = []
    in_section = False
    for raw in content.decode("utf-8", "replace").splitlines():
        line = raw.strip()
        if line.startswith("["):
            in_section = line == "[HitObjects]"
            continue
        if not in_section or not line:
            continue
        parts = line.split(",")
        if len(parts) < 4:
            continue
        t, flags = int(float(parts[2])), int(parts[3])
        kind = 3 if flags & 8 else 1 if flags & 2 else 0
        objects.append((t, kind))
    objects.sort()
    return objects


def pattern_features(content: bytes, clock_rate: float = 1.0) -> dict:
    """Mesure la part de streams, le plus long stream, sa vitesse et la durée de la map."""
    objs = parse_hit_objects(content)
    n = len(objs)
    if n < 2:
        return {"length_s": 0.0, "stream_frac": 0.0, "long_stream_frac": 0.0,
                "max_run": 0, "stream_bpm": 0.0, "n_objects": n}

    runs: list[list[float]] = []  # chaque run = liste des écarts (ms) entre notes consécutives
    current: list[float] = []
    for (t0, k0), (t1, k1) in zip(objs, objs[1:]):
        dt = (t1 - t0) / clock_rate
        ok = k0 != 3 and k1 != 3 and MIN_DT_MS <= dt <= STREAM_DT_MS
        if ok and current and abs(dt - current[0]) > 0.2 * current[0]:
            ok = False  # changement de rythme : on coupe le run
            runs.append(current)
            current = [dt]
            continue
        if ok:
            current.append(dt)
        else:
            if current:
                runs.append(current)
            current = []
    if current:
        runs.append(current)

    # Les triolets sont partout : on ne compte comme stream que les séries de 5 notes et plus.
    burst_runs = [r for r in runs if len(r) + 1 >= 5]
    notes_in_streams = sum(len(r) + 1 for r in burst_runs)
    notes_in_long = sum(len(r) + 1 for r in burst_runs if len(r) + 1 >= 12)
    dts = [dt for r in burst_runs for dt in r]
    length_s = (objs[-1][0] - objs[0][0]) / clock_rate / 1000
    return {
        "length_s": round(length_s, 1),
        "stream_frac": round(notes_in_streams / n, 3),
        "long_stream_frac": round(notes_in_long / n, 3),
        "max_run": max((len(r) + 1 for r in burst_runs), default=0),
        "stream_bpm": round(15000 / statistics.median(dts), 1) if dts else 0.0,
        "n_objects": n,
    }
