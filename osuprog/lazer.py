"""Import des parties enregistrées localement par osu! lazer (fichier client.realm).

Lazer garde sur le PC chaque partie terminée, y compris celles qu'osu! ne conserve
pas en ligne (passes moins bonnes, maps non classées, parties hors connexion).
Le fichier est lu via Node.js (tools/lazer-export), toujours sur une copie.
"""
import configparser
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from . import db
from .config import ROOT
from .osufile import osu_path

EXPORTER = ROOT / "tools" / "lazer-export"


def lazer_dir() -> Path | None:
    """Dossier de données de lazer (%APPDATA%\\osu, ou celui choisi dans storage.ini)."""
    base = Path(os.environ.get("APPDATA", "")) / "osu"
    ini = base / "storage.ini"
    if ini.exists():
        parser = configparser.ConfigParser()
        parser.read_string("[s]\n" + ini.read_text(encoding="utf-8", errors="replace"))
        custom = parser.get("s", "FullPath", fallback="").strip()
        if custom and (Path(custom) / "client.realm").exists():
            return Path(custom)
    return base if (base / "client.realm").exists() else None


def available() -> str | None:
    """None si l'import est possible, sinon la raison pour laquelle il ne l'est pas."""
    if lazer_dir() is None:
        return "osu! lazer introuvable sur ce PC"
    if shutil.which("node") is None:
        return "Node.js n'est pas installé"
    if not (EXPORTER / "node_modules" / "realm").exists():
        return "module Realm absent (lance « npm install » dans tools/lazer-export)"
    return None


def export_scores() -> list[dict]:
    """Copie client.realm dans un dossier temporaire et en extrait les scores."""
    source = lazer_dir() / "client.realm"
    with tempfile.TemporaryDirectory(prefix="osu-lazer-") as tmp:
        copy = Path(tmp) / "client.realm"
        shutil.copy2(source, copy)
        result = subprocess.run(
            ["node", str(EXPORTER / "export.js"), str(copy)],
            cwd=EXPORTER, capture_output=True, timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip() or "échec de l'export lazer")
    return json.loads(result.stdout.decode("utf-8"))


def local_score_id(score: dict) -> int:
    """Identifiant du score : celui d'osu! s'il a été envoyé en ligne, sinon un négatif stable."""
    if score["online_id"] > 0:
        return score["online_id"]
    return -(10**12 + uuid.UUID(score["uuid"]).int % 10**11)


def copy_osu_file(score: dict) -> None:
    """Récupère le .osu depuis les fichiers de lazer : pas besoin de le télécharger."""
    target = osu_path(score["beatmap_id"])
    h = score.get("beatmap_hash") or ""
    source = lazer_dir() / "files" / h[:1] / h[:2] / h
    if h and not target.exists() and source.exists():
        shutil.copyfile(source, target)


def import_local(conn, user_id: int, store_score, log=print) -> int:
    """Ajoute les parties locales de lazer (les tiennes et celles jouées hors connexion)."""
    reason = available()
    if reason:
        log(f"Parties locales de lazer non lues : {reason}.")
        return 0
    scores = [s for s in export_scores() if s["user_id"] in (user_id, 0) and s["beatmap_id"] > 0]
    known = {r["beatmap_id"] for r in conn.execute("SELECT beatmap_id FROM beatmaps")}
    new = 0
    for s in scores:
        if s["beatmap_id"] not in known:  # le statut en ligne est plus à jour que celui de lazer
            db.upsert_beatmap(
                conn,
                {"id": s["beatmap_id"], "beatmapset_id": s["beatmapset_id"], "version": s["version"],
                 "status": s["status"]},
                {"artist": s["artist"], "title": s["title"], "creator": s["creator"]},
            )
            known.add(s["beatmap_id"])
        copy_osu_file(s)
        new += store_score(conn, {
            "id": local_score_id(s),
            "beatmap_id": s["beatmap_id"],
            "mods": s["mods"],
            "accuracy": s["accuracy"],
            "max_combo": s["max_combo"],
            "statistics": s["statistics"],
            "passed": s["passed"],
            "rank": s["rank"],
            "pp": s["pp"],
            "ended_at": s["ended_at"],
        }, "lazer_local")
    conn.commit()
    log(f"{len(scores)} parties trouvées dans lazer sur ce PC, {new} nouvelles.")
    return new
