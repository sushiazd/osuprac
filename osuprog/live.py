"""Suivi en direct : on synchronise dès que lazer enregistre une nouvelle partie.

Lazer garde client.realm ouvert en mémoire : sa date de modification ne bouge pas tant que le jeu tourne
(constaté le 2026-10-04 : restée à l'heure du lancement pendant toute une séance). En revanche, chaque partie
terminée dépose son replay dans files/<x>/<xy>/, ce qui met à jour la date du sous-dossier. On surveille donc
la plus récente de ces dates (≈ 20 ms pour 256 dossiers). Un téléchargement de map déclenche aussi une
synchro : c'est sans conséquence.

Le tableau de bord appelle `check()` toutes les 10 s : la map jouée se coche et la suivante s'affiche.
"""
import os
import time

from . import db, lazer

MIN_GAP_S = 30        # au plus une synchro toutes les 30 s
SETTLE_S = 6          # laisse le temps à lazer d'envoyer la partie en ligne avant de lire


def realm_mtime() -> float | None:
    """Date de client.realm : sert à savoir si lazer est installé sur ce PC."""
    folder = lazer.lazer_dir()
    if folder is None:
        return None
    try:
        return (folder / "client.realm").stat().st_mtime
    except OSError:
        return None


def newest_subfolder_mtime(files_dir) -> float:
    """Date de modification la plus récente parmi les sous-dossiers files/<x>/<xy> de lazer."""
    newest = 0.0
    try:
        with os.scandir(files_dir) as top:
            for first in top:
                if not first.is_dir():
                    continue
                with os.scandir(first.path) as sub:
                    for second in sub:
                        if second.is_dir():
                            # os.stat et pas second.stat() : la date vue depuis le dossier parent peut être en
                            # retard sous Windows. Coût mesuré : ~22 ms pour les 256 dossiers de lazer.
                            newest = max(newest, os.stat(second.path).st_mtime)
    except OSError:
        pass
    return newest


def activity_mtime() -> float | None:
    """Dernière activité enregistrée par lazer : nouveau fichier (replay, map) ou écriture de client.realm."""
    folder = lazer.lazer_dir()
    if folder is None:
        return None
    return max(realm_mtime() or 0.0, newest_subfolder_mtime(folder / "files")) or None


def due(mtime: float | None, seen: float | None, last_try: float | None, now: float) -> bool:
    """Faut-il synchroniser ? lazer a écrit depuis la dernière lecture, l'écriture s'est stabilisée, et la
    dernière tentative n'est pas trop récente."""
    if mtime is None or (seen is not None and mtime <= seen + 1):
        return False
    if now - mtime < SETTLE_S:
        return False
    return last_try is None or now - last_try >= MIN_GAP_S


def pending(conn) -> bool:
    """Une nouvelle partie est-elle probable (lazer a écrit depuis la dernière lecture) ?"""
    mtime = activity_mtime()
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
    db.set_meta(conn, "realm_seen", activity_mtime())
    conn.commit()
    if run_sync() is False:  # échec (réseau…) : on retentera après MIN_GAP_S
        db.set_meta(conn, "realm_seen", previous)
        conn.commit()
    return True
