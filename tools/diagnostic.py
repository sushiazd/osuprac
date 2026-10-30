"""Bilan des maps proposées par l'outil, à envoyer au développeur (lecture seule, rien n'est modifié ni envoyé).

    python tools/diagnostic.py                      # depuis hier 18h
    python tools/diagnostic.py --depuis "2026-10-29 18:00"

Écrit `bilan-osu.txt` (dans le dossier courant) : niveau estimé, maps proposées, résultats réels, précision par étoiles.
Ne contient que des stats de jeu : aucun mot de passe, aucune clé, aucun fichier personnel.
"""
import argparse
import json
import os
import sqlite3
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path


def find_db() -> Path:
    for base in (os.getenv("OSU_DATA_DIR"), Path(os.getenv("LOCALAPPDATA", "")) / "osu-progression",
                 Path(__file__).resolve().parent.parent / "data"):
        if base and (Path(base) / "osu.sqlite").exists():
            return Path(base) / "osu.sqlite"
    sys.exit("Base introuvable : lance d'abord l'outil au moins une fois (lancer.bat).")


def open_ro(path: Path) -> sqlite3.Connection:
    """Copie en mémoire via l'API de sauvegarde (lecture seule : le tableau de bord peut rester ouvert)."""
    src = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=30)
    mem = sqlite3.connect(":memory:")
    src.backup(mem)
    src.close()
    mem.row_factory = sqlite3.Row
    return mem


def meta(conn, key):
    r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else None


def local(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)


def map_info(conn, bid, mods_key=None):
    row = None
    if mods_key is not None:
        row = conn.execute("SELECT stars, attrs FROM map_attrs WHERE beatmap_id=? AND mods_key=?", (bid, mods_key)).fetchone()
    row = row or conn.execute("SELECT stars, attrs FROM map_attrs WHERE beatmap_id=? ORDER BY mods_key LIMIT 1", (bid,)).fetchone()
    b = conn.execute("SELECT artist, title, version FROM beatmaps WHERE beatmap_id=?", (bid,)).fetchone()
    hp = None
    if row and row["attrs"]:
        try:
            hp = json.loads(row["attrs"]).get("hp")
        except Exception:
            pass
    name = f"{b['artist']} - {b['title']} [{b['version']}]" if b else f"#{bid}"
    return (row["stars"] if row else None), hp, name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--depuis", help='ex. "2026-10-29 18:00" (heure locale)')
    ap.add_argument("--sortie", default="bilan-osu.txt")
    args = ap.parse_args()
    since = datetime.fromisoformat(args.depuis) if args.depuis else (datetime.now() - timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)

    db = find_db()
    conn = open_ro(db)
    out = []
    w = out.append
    w(f"Bilan généré le {datetime.now():%Y-%m-%d %H:%M} — base : {db} — depuis {since:%Y-%m-%d %H:%M}\n")

    w("== Niveau estimé par l'outil ==")
    for k in ("user", "last_sync", "criteria_version", "classifier_version", "clear_level", "clean_level",
              "calibration", "casual_fails", "plan_summary", "session_variant", "session_extra", "ignored_maps"):
        w(f"{k}: {meta(conn, k)}")
    w("")
    try:
        for r in conn.execute("SELECT category, rating, level, games FROM ratings ORDER BY category"):
            w(f"  {r['category']:<12} note={r['rating']:.2f}★  niveau commun={'-' if r['level'] is None else format(r['level'], '.2f')}  parties={r['games']:.0f}")
    except sqlite3.Error as e:
        w(f"  (ratings illisibles : {e})")
    w("")

    w("== Maps proposées (plan du jour) et résultat de la 1re tentative ==")
    days = sorted({since.date().isoformat(), datetime.now().date().isoformat()} |
                  {r["day"] for r in conn.execute("SELECT DISTINCT day FROM session_plan WHERE day>=?", (since.date().isoformat(),))})
    for day in days:
        items = conn.execute("SELECT * FROM session_plan WHERE day=? ORDER BY pos", (day,)).fetchall()
        if not items:
            continue
        w(f"-- {day}")
        for it in items:
            stars, hp, name = map_info(conn, it["beatmap_id"])
            first = None
            for s in conn.execute("SELECT * FROM scores WHERE beatmap_id=? ORDER BY ended_at", (it["beatmap_id"],)):
                if s["ended_at"] and local(s["ended_at"]).date().isoformat() == day:
                    first = s
                    break
            res = "pas jouée"
            if first:
                n = first["n_great"] + first["n_ok"] + first["n_meh"] + first["n_miss"]
                res = f"{'passée' if first['passed'] else 'ÉCHEC'} {first['accuracy'] * 100 if first['accuracy'] <= 1 else first['accuracy']:.1f}% {first['n_miss']} miss/{n}"
            keys = it.keys()
            p = it["p"] if "p" in keys and it["p"] is not None else None
            mn = it["minutes"] if "minutes" in keys and it["minutes"] is not None else None
            w(f"  {it['pos']:>2} {it['role'] or '':<10} {it['category'] or '':<10} "
              f"{'?' if stars is None else format(stars, '.2f')}★ HP{'?' if hp is None else format(hp, 'g')} "
              f"p={'-' if p is None else format(p, '.2f')} {'-' if mn is None else format(mn, '.0f')}min | {name[:60]} | {res}")
    w("")

    w("== Toutes les parties depuis la date donnée ==")
    rows = conn.execute("SELECT * FROM scores WHERE ended_at IS NOT NULL ORDER BY ended_at").fetchall()
    recent = [r for r in rows if local(r["ended_at"]) >= since]
    for s in recent:
        stars, hp, name = map_info(conn, s["beatmap_id"], s["mods_key"])
        n = s["n_great"] + s["n_ok"] + s["n_meh"] + s["n_miss"]
        acc = s["accuracy"] * 100 if s["accuracy"] <= 1 else s["accuracy"]
        try:
            ab = conn.execute("SELECT abandoned FROM scores WHERE score_id=?", (s["score_id"],)).fetchone()[0]
        except sqlite3.Error:
            ab = "?"
        w(f"  {local(s['ended_at']):%m-%d %H:%M} {'-' if stars is None else format(stars, '.2f')}★ HP{'?' if hp is None else format(hp, 'g')} "
          f"{'pass' if s['passed'] else 'FAIL'} {acc:5.1f}% {s['n_miss']:>3}miss/{n:<4} mods={s['mods_key'] or '-'} "
          f"quitté={ab} {s['source'] or ''} | {name[:50]}")
    w(f"({len(recent)} parties)\n")

    w("== Précision et réussite par tranche d'étoiles (tout l'historique, hors parties quittées) ==")
    bands = {}
    for s in rows:
        try:
            if conn.execute("SELECT abandoned FROM scores WHERE score_id=?", (s["score_id"],)).fetchone()[0]:
                continue
        except sqlite3.Error:
            pass
        stars, _, _ = map_info(conn, s["beatmap_id"], s["mods_key"])
        if stars is None:
            continue
        acc = s["accuracy"] * 100 if s["accuracy"] <= 1 else s["accuracy"]
        bands.setdefault(int(stars * 2) / 2, []).append((s["passed"], acc))
    w("  tranche   parties  réussies  précision médiane")
    for b in sorted(bands):
        v = bands[b]
        w(f"  {b:.1f}-{b + 0.5:.1f}★  {len(v):>6}  {100 * sum(1 for p, _ in v if p) / len(v):>6.0f}%  {statistics.median(a for _, a in v):>8.1f}%")

    Path(args.sortie).write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"OK -> {Path(args.sortie).resolve()}\nEnvoie ce fichier.")


if __name__ == "__main__":
    main()
