"""Suivi en direct : lazer réécrit client.realm à la fin de chaque partie ; on synchronise dès qu'il change.

Le tableau de bord appelle `check()` toutes les quelques secondes : la map jouée se coche et la suivante
s'affiche sans cliquer sur « Synchroniser ».
"""
import time

from . import db, lazer

MIN_GAP_S = 45        # au plus une synchro toutes les 45 s (lazer écrit aussi le fichier pour d'autres raisons)
SETTLE_S = 8          # laisse le temps à lazer d'envoyer la partie en ligne avant de lire


def realm_mtime() -> float | None:
    folder = lazer.lazer_dir()
    if folder is None:
        return None
    try:
        return (folder / "client.realm").stat().st_mtime
    except OSError:
        return None


def due(mtime: float | None, seen: float | None, last_try: float | None, now: float) -> bool:
    """Faut-il synchroniser ? Le fichier a changé depuis la dernière lecture, s'est stabilisé, et la dernière
    tentative n'est pas trop récente."""
    if mtime is None or (seen is not None and mtime <= seen + 1):
        return False
    if now - mtime < SETTLE_S:
        return False
    return last_try is None or now - last_try >= MIN_GAP_S


def pending(conn) -> bool:
    """Une nouvelle partie est-elle probable (fichier de lazer modifié depuis la dernière lecture) ?"""
    mtime = realm_mtime()
    if mtime is not None and db.get_meta(conn, "realm_seen") is None:
        db.set_meta(conn, "realm_seen", mtime)  # premier passage : rien à rattraper, la synchro horaire l'a fait
        conn.commit()
        return False
    return due(mtime, db.get_meta(conn, "realm_seen"), db.get_meta(conn, "live_last_try"), time.time())


def check(conn, run_sync) -> bool:
    """Lance `run_sync()` si une nouvelle partie est probable. Renvoie True si une synchro a été lancée."""
    if not pending(conn):
        return False
    previous = db.get_meta(conn, "realm_seen")
    db.set_meta(conn, "live_last_try", time.time())
    db.set_meta(conn, "realm_seen", realm_mtime())
    conn.commit()
    if run_sync() is False:  # échec (réseau…) : on retentera après MIN_GAP_S
        db.set_meta(conn, "realm_seen", previous)
        conn.commit()
    return True
