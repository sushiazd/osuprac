"""Synchronisation silencieuse, lancée par la tâche planifiée Windows (voir activer-synchro-auto.bat).

.pyw + pythonw.exe : aucune fenêtre ne s'ouvre. Le résultat est écrit dans data/sync.log.
"""
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from osuprog.config import DATA_DIR  # noqa: E402
from osuprog.sync import sync  # noqa: E402

LOG = DATA_DIR / "sync.log"
MAX_LINES = 2000


def main() -> None:
    lines = []

    def log(message: str) -> None:
        lines.append(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {message}")

    try:
        sync(log=log)
    except Exception:
        log("ÉCHEC : " + traceback.format_exc().strip().replace("\n", "\n    "))
    old = LOG.read_text(encoding="utf-8").splitlines() if LOG.exists() else []
    LOG.write_text("\n".join((old + lines)[-MAX_LINES:]) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
