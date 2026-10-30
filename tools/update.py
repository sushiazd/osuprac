"""Mise à jour automatique au lancement (appelée par lancer.bat). Silencieuse si hors-ligne ou en cas d'erreur.

Avec git (dossier cloné) : `git pull --ff-only`. Sinon (ZIP téléchargé) : récupère la dernière version sur GitHub
et remplace uniquement les fichiers du dépôt qui ont changé ; ses données, son .env et son .venv ne sont pas touchés.
"""
import io
import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO = "sushiazd/osuprac"
ROOT = Path(__file__).resolve().parent.parent
VERSION = ROOT / ".version"
SKIP = (".env", ".venv/", "data/", "sauvegardes/", ".git/", "tools/lazer-export/node_modules/")


def get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "osuprac-update"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def via_git() -> None:
    before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=20).stdout
    subprocess.run(["git", "pull", "--ff-only", "-q"], cwd=ROOT, capture_output=True, timeout=120)
    after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=20).stdout
    if before != after:
        print("Mise à jour installée (git).")


def via_zip() -> None:
    sha = json.loads(get(f"https://api.github.com/repos/{REPO}/commits/main", 10))["sha"]
    if VERSION.exists() and VERSION.read_text().strip() == sha:
        return
    print("Téléchargement de la mise à jour...")
    z = zipfile.ZipFile(io.BytesIO(get(f"https://github.com/{REPO}/archive/refs/heads/main.zip")))
    changed = 0
    for info in z.infolist():
        rel = info.filename.split("/", 1)[1] if "/" in info.filename else ""
        if not rel or info.is_dir() or rel.startswith(SKIP) or rel == ".env":
            continue
        data = z.read(info)
        dest = ROOT / rel
        if dest.exists() and dest.read_bytes() == data:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        changed += 1
    VERSION.write_text(sha)
    print(f"Mise à jour installée ({changed} fichiers).")


def main() -> None:
    try:
        if (ROOT / ".git").exists() and shutil.which("git"):
            via_git()
        else:
            via_zip()
    except Exception as e:  # hors-ligne, GitHub indisponible... : on lance l'outil tel quel
        print(f"(mise à jour ignorée : {e})")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
