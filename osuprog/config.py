"""Chemins et réglages, lus depuis le fichier .env à la racine du projet."""
import os
import shutil
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Les données vivent hors du dossier du projet : un dossier synchronisé (OneDrive…) peut verrouiller ou
# dupliquer la base pendant qu'elle est écrite. Des sauvegardes datées restent dans le projet (BACKUP_DIR).
LEGACY_DATA_DIR = ROOT / "data"
_local_app_data = os.getenv("LOCALAPPDATA")
DEFAULT_DATA_DIR = Path(_local_app_data) / "osu-progression" if _local_app_data else LEGACY_DATA_DIR
DATA_DIR = Path(os.getenv("OSU_DATA_DIR") or DEFAULT_DATA_DIR)
MAPS_DIR = DATA_DIR / "maps"
DB_PATH = DATA_DIR / "osu.sqlite"
BACKUP_DIR = ROOT / "sauvegardes"

OSU_CLIENT_ID = os.getenv("OSU_CLIENT_ID", "").strip()
OSU_CLIENT_SECRET = os.getenv("OSU_CLIENT_SECRET", "").strip()
OSU_USERNAME = os.getenv("OSU_USERNAME", "").strip()


def credentials() -> tuple[str, str]:
    """Relit .env à chaque appel : une clé corrigée est prise en compte sans redémarrer."""
    load_dotenv(ROOT / ".env", override=True)
    return os.getenv("OSU_CLIENT_ID", "").strip(), os.getenv("OSU_CLIENT_SECRET", "").strip()


def migrate_legacy_data() -> bool:
    """Déplace une seule fois les données de l'ancien emplacement (data/ dans le projet) vers DATA_DIR.

    L'ancien dossier est laissé tel quel : rien n'est supprimé. La base est copiée par l'API de sauvegarde
    de SQLite (copie cohérente même si un autre programme l'utilise), puis renommée d'un coup.
    """
    old_db = LEGACY_DATA_DIR / "osu.sqlite"
    if DATA_DIR.resolve() == LEGACY_DATA_DIR.resolve() or DB_PATH.exists() or not old_db.exists():
        return False
    MAPS_DIR.mkdir(parents=True, exist_ok=True)
    old_maps = LEGACY_DATA_DIR / "maps"
    if old_maps.exists():
        for f in old_maps.iterdir():
            target = MAPS_DIR / f.name
            if f.is_file() and not target.exists():
                shutil.copyfile(f, target)
    for name in ("sync.log",):
        if (LEGACY_DATA_DIR / name).exists() and not (DATA_DIR / name).exists():
            shutil.copyfile(LEGACY_DATA_DIR / name, DATA_DIR / name)
    tmp = DB_PATH.with_name(f"osu.sqlite.{os.getpid()}.tmp")
    source, target = sqlite3.connect(old_db), sqlite3.connect(tmp)
    source.backup(target)
    source.close()
    target.close()
    if DB_PATH.exists():  # un autre processus a migré en même temps
        tmp.unlink()
        return False
    os.replace(tmp, DB_PATH)
    return True


DATA_DIR.mkdir(parents=True, exist_ok=True)
MAPS_DIR.mkdir(parents=True, exist_ok=True)
migrate_legacy_data()
