"""Synchronisation silencieuse, lancée par la tâche planifiée Windows (voir activer-synchro-auto.bat).

.pyw + pythonw.exe : aucune fenêtre ne s'ouvre. Le résultat est écrit dans sync.log (dossier des données) ;
un palier validé ou un succès débloqué s'affiche en notification Windows. Sans fenêtre, une erreur serait
invisible : même un échec au chargement du code est donc écrit dans le journal.
"""
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
MAX_LINES = 2000
FALLBACK_LOG = Path(os.environ.get("LOCALAPPDATA") or ROOT) / "osu-progression" / "sync.log"


def main() -> None:
    lines = []

    def log(message: str) -> None:
        lines.append(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {message}")

    log_path = FALLBACK_LOG
    try:
        from osuprog import notify
        from osuprog.config import DATA_DIR
        from osuprog.db import SyncBusy
        from osuprog.sync import sync

        log_path = DATA_DIR / "sync.log"
        try:
            result = sync(log=log)
            notify.announce(result.get("events", []))
        except SyncBusy:
            log("Synchronisation déjà en cours (tableau de bord) : rien à faire.")
    except Exception:
        log("ÉCHEC : " + traceback.format_exc().strip().replace("\n", "\n    "))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    old = log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []
    log_path.write_text("\n".join((old + lines)[-MAX_LINES:]) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
