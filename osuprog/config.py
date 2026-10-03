"""Chemins et réglages, lus depuis le fichier .env à la racine du projet."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.getenv("OSU_DATA_DIR") or ROOT / "data")
MAPS_DIR = DATA_DIR / "maps"
DB_PATH = DATA_DIR / "osu.sqlite"

OSU_CLIENT_ID = os.getenv("OSU_CLIENT_ID", "").strip()
OSU_CLIENT_SECRET = os.getenv("OSU_CLIENT_SECRET", "").strip()
OSU_USERNAME = os.getenv("OSU_USERNAME", "").strip()


def credentials() -> tuple[str, str]:
    """Relit .env à chaque appel : une clé corrigée est prise en compte sans redémarrer."""
    load_dotenv(ROOT / ".env", override=True)
    return os.getenv("OSU_CLIENT_ID", "").strip(), os.getenv("OSU_CLIENT_SECRET", "").strip()


DATA_DIR.mkdir(parents=True, exist_ok=True)
MAPS_DIR.mkdir(parents=True, exist_ok=True)
