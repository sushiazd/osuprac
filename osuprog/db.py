"""Base SQLite locale : scores, attributs des maps, notes et paliers."""
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone

from .config import BACKUP_DIR, DATA_DIR, DB_PATH

BACKUPS_KEPT = 7

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS beatmaps (
    beatmap_id INTEGER PRIMARY KEY,
    beatmapset_id INTEGER,
    artist TEXT, title TEXT, version TEXT, creator TEXT,
    status TEXT,
    playcount INTEGER DEFAULT 0,
    favourites INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS map_attrs (
    beatmap_id INTEGER,
    mods_key TEXT,
    stars REAL,
    primary_cat TEXT,
    attrs TEXT,
    weights TEXT,
    PRIMARY KEY (beatmap_id, mods_key)
);

CREATE TABLE IF NOT EXISTS scores (
    score_id INTEGER PRIMARY KEY,
    beatmap_id INTEGER,
    mods TEXT,
    mods_key TEXT,
    accuracy REAL,
    max_combo INTEGER,
    n_great INTEGER, n_ok INTEGER, n_meh INTEGER, n_miss INTEGER,
    passed INTEGER,
    grade TEXT,
    pp REAL,
    ended_at TEXT,
    source TEXT,
    countable INTEGER
);

-- Nombre de parties par map depuis ton inscription (osu! ne garde pas chaque partie).
CREATE TABLE IF NOT EXISTS playcounts (
    beatmap_id INTEGER PRIMARY KEY,
    count INTEGER
);

CREATE TABLE IF NOT EXISTS ratings (
    category TEXT PRIMARY KEY,
    rating REAL,
    games REAL
);

CREATE TABLE IF NOT EXISTS rating_events (
    score_id INTEGER,
    category TEXT,
    weight REAL,
    expected REAL,
    outcome REAL,
    delta REAL,
    rating_after REAL,
    PRIMARY KEY (score_id, category)
);

-- Palier validé le plus haut par compétence, en centièmes d'étoile (425 = 4,25★).
CREATE TABLE IF NOT EXISTS progress (
    category TEXT PRIMARY KEY,
    validated_c INTEGER
);

CREATE TABLE IF NOT EXISTS tier_log (
    category TEXT,
    level_c INTEGER,
    how TEXT,
    at TEXT,
    PRIMARY KEY (category, level_c)
);

-- Maps imposées pour un palier (category = 'exam' pour l'examen de rang).
CREATE TABLE IF NOT EXISTS tier_maps (
    category TEXT,
    level_c INTEGER,
    slot INTEGER,
    beatmap_id INTEGER,
    PRIMARY KEY (category, level_c, slot)
);

-- Recherches de maps déjà faites (page suivante via le curseur).
CREATE TABLE IF NOT EXISTS pool_queries (
    query TEXT PRIMARY KEY,
    pages INTEGER,
    cursor TEXT,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS session_plan (
    day TEXT,
    pos INTEGER,
    role TEXT,
    category TEXT,
    level_c INTEGER,
    beatmap_id INTEGER,
    PRIMARY KEY (day, pos)
);
"""


# Colonnes ajoutées après coup : (table, colonne, déclaration). Ajoutées à la volée aux bases existantes.
ADDED_COLUMNS = [
    ("session_plan", "reason", "TEXT"),
    ("session_plan", "p", "REAL"),
    ("session_plan", "minutes", "REAL"),
    ("session_plan", "share", "REAL"),
]


def connect(path=None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL : le tableau de bord lit pendant que la synchro écrit, sans « database is locked ».
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    for table, column, decl in ADDED_COLUMNS:
        if column not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    return conn


def local_day(iso: str) -> str:
    """Jour local (AAAA-MM-JJ) d'une date ISO UTC de l'API osu!."""
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().date().isoformat()


def daily_backup(conn, folder=BACKUP_DIR, keep: int = BACKUPS_KEPT) -> bool:
    """Une copie de la base par jour dans le dossier du projet (synchronisé par OneDrive, lui) ; garde les `keep`
    dernières. Le fichier est écrit à côté puis renommé : OneDrive ne voit jamais une copie à moitié écrite."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"osu-{date.today().isoformat()}.sqlite"
    if target.exists():
        return False
    tmp = target.with_suffix(".tmp")
    out = sqlite3.connect(tmp)
    conn.backup(out)
    out.close()
    os.replace(tmp, target)
    for old in sorted(folder.glob("osu-????-??-??.sqlite"))[:-keep]:
        old.unlink()
    return True


class SyncBusy(RuntimeError):
    pass


@contextmanager
def sync_lock(stale_after: float = 600):
    """Empêche deux synchronisations en même temps (bouton du tableau de bord, suivi en direct, tâche horaire)."""
    path = DATA_DIR / "sync.lock"
    try:
        if path.exists() and time.time() - path.stat().st_mtime > stale_after:
            path.unlink()  # verrou laissé par une synchro plantée
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SyncBusy("Une synchronisation est déjà en cours.") from None
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)


def now_iso() -> str:
    """Même format que les dates de l'API osu! (comparables en tant que texte)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_meta(conn, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_meta(conn, key: str, value) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


def del_meta(conn, key: str) -> None:
    conn.execute("DELETE FROM meta WHERE key = ?", (key,))


def upsert_beatmap(conn, beatmap: dict, beatmapset: dict) -> None:
    conn.execute(
        """INSERT INTO beatmaps (beatmap_id, beatmapset_id, artist, title, version, creator,
                                 status, playcount, favourites)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(beatmap_id) DO UPDATE SET
               status = excluded.status,
               playcount = MAX(beatmaps.playcount, excluded.playcount),
               favourites = MAX(beatmaps.favourites, excluded.favourites)""",
        (
            beatmap["id"],
            beatmap.get("beatmapset_id") or beatmapset.get("id"),
            beatmapset.get("artist", ""),
            beatmapset.get("title", ""),
            beatmap.get("version", ""),
            beatmapset.get("creator", ""),
            beatmap.get("status") or beatmapset.get("status", ""),
            beatmap.get("playcount") or 0,
            beatmapset.get("favourite_count") or 0,
        ),
    )
