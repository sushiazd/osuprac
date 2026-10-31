"""Suivi en direct : on synchronise dès que lazer enregistre une nouvelle partie.

Lazer garde client.realm ouvert en mémoire : sa date de modification ne bouge pas tant que le jeu tourne
(constaté le 2026-10-04 : restée à l'heure du lancement pendant toute une séance). En revanche, chaque partie
terminée dépose son replay dans files/<x>/<xy>/, ce qui met à jour la date du sous-dossier. On surveille donc
la plus récente de ces dates (≈ 20 ms pour 256 dossiers). Un téléchargement de map déclenche aussi une
synchro : c'est sans conséquence.

Le tableau de bord regarde toutes les POLL_S secondes. Une nouvelle partie déclenche d'abord une synchro express
(parties locales de lazer seulement, ~1 s) : la map jouée se coche et la suivante s'affiche. La synchro complète
(pp et rang d'osu!, échecs, historique en ligne) suit ONLINE_DELAY_S plus tard, en arrière-plan : lazer a eu le
temps d'envoyer la partie, et si la synchro express l'a ratée, celle-ci la rattrape.
"""
import os
import threading
import time

from . import db, lazer

POLL_S = 1            # le tableau de bord regarde chaque seconde (~25 ms par regard)
MIN_GAP_S = 5         # au plus une synchro express toutes les 5 s
SETTLE_S = 1          # laisse lazer finir d'écrire la partie (replay puis client.realm)
ONLINE_DELAY_S = 20   # synchro complète après la synchro express
ONLINE_RETRY_S = 60   # synchro complète échouée (réseau, synchro horaire en cours) : on retente

_online = {"thread": None, "finished": False}


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
    """Lance `run_sync()` (la synchro express) si une nouvelle partie est probable et programme la synchro
    complète. Renvoie True si une synchro a été lancée."""
    if not pending(conn):
        return False
    previous = db.get_meta(conn, "realm_seen")
    db.set_meta(conn, "live_last_try", time.time())
    db.set_meta(conn, "realm_seen", activity_mtime())
    db.set_meta(conn, "live_online_due", time.time() + ONLINE_DELAY_S)
    conn.commit()
    if run_sync() is False:  # échec (réseau…) : on retentera après MIN_GAP_S
        db.set_meta(conn, "realm_seen", previous)
        conn.commit()
    return True


def online_due(conn) -> bool:
    """La synchro complète qui suit une synchro express est-elle à lancer ?"""
    due_at = db.get_meta(conn, "live_online_due")
    running = _online["thread"] is not None and _online["thread"].is_alive()
    return due_at is not None and time.time() >= due_at and not running


def start_online(conn, run_sync) -> None:
    """Lance `run_sync()` (la synchro complète) dans un fil à part : le tableau de bord reste utilisable pendant
    ces quelques secondes. En cas d'échec (`run_sync()` renvoie False), elle est retentée plus tard."""
    db.set_meta(conn, "live_online_due", None)
    conn.commit()

    def work() -> None:
        if run_sync() is False:
            c = db.connect()
            db.set_meta(c, "live_online_due", time.time() + ONLINE_RETRY_S)
            c.commit()
            c.close()
        else:
            _online["finished"] = True

    _online["thread"] = threading.Thread(target=work, name="osu-sync-complete", daemon=True)
    _online["thread"].start()


def online_finished() -> bool:
    """True une seule fois après chaque synchro complète réussie : le tableau de bord se redessine alors."""
    finished, _online["finished"] = _online["finished"], False
    return finished
