"""Synchronisation : récupère tes scores, recalcule tes notes et met à jour les paliers."""
import json
import time
from datetime import date

from . import db, lazer, model, planner, progression, rating, rewards
from . import mods as modlib
from .api import OsuApi
from .config import OSU_USERNAME
from .maps import (backfill_hp, ensure_attrs_many, load_bundled_pool, recompute_if_engine_changed,
                   reclassify_if_needed)

# Statuts pour lesquels osu! enregistre les scores : ailleurs, « pas de score » ne veut rien dire.
SCORED_STATUSES = ("ranked", "approved", "loved", "qualified")
# À partir de combien d'essais sans aucun score on considère la map comme jamais passée.
INFERRED_FAIL_MIN_PLAYS = 3
INFERRED_FAIL_DATE = "2000-01-01T00:00:00Z"  # date inconnue : rangé avant tout le reste
DUPLICATE_WINDOW_S = 15  # deux parties de la même map à moins de 15 s d'écart = la même partie


def store_score(conn, s: dict, source: str) -> bool:
    """Enregistre un score au format API lazer. Renvoie True s'il est nouveau."""
    mods = [{"acronym": m["acronym"], **({"settings": m["settings"]} if m.get("settings") else {})}
            for m in s.get("mods", [])]
    stats = s.get("statistics") or {}
    if s.get("beatmap"):
        db.upsert_beatmap(conn, s["beatmap"], s.get("beatmapset") or {})
    # Une partie lue dans lazer avant d'avoir reçu son identifiant en ligne (id négatif) et la même partie
    # revenue de l'API : on garde la version en ligne, jamais les deux.
    twin = """SELECT score_id FROM scores WHERE beatmap_id = ? AND score_id {} 0 AND source != 'inferred_fail'
              AND abs(julianday(ended_at) - julianday(?)) * 86400 < ?"""
    if s["id"] < 0 and conn.execute(twin.format(">"), (s["beatmap_id"], s["ended_at"], DUPLICATE_WINDOW_S)).fetchone():
        return False
    if s["id"] > 0:
        conn.execute(f"DELETE FROM scores WHERE score_id IN ({twin.format('<')})",
                     (s["beatmap_id"], s["ended_at"], DUPLICATE_WINDOW_S))
    cur = conn.execute(
        """INSERT OR IGNORE INTO scores VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            s["id"],
            s["beatmap_id"],
            json.dumps(mods),
            modlib.mods_key(mods),
            round(s["accuracy"] * 100, 2),
            s.get("max_combo", 0),
            stats.get("great", 0),
            stats.get("ok", 0),
            stats.get("meh", 0),
            stats.get("miss", 0),
            int(bool(s.get("passed"))),
            s.get("rank", ""),
            s.get("pp"),
            s["ended_at"],
            source,
            int(modlib.is_countable(mods)),
        ),
    )
    return cur.rowcount == 1


def import_history(conn, api, user_id: int, log=print) -> int:
    """Récupère tout ce qu'osu! garde de ton historique.

    osu! ne stocke pas chaque partie : seulement le nombre de parties par map et
    tes meilleurs scores sur chacune. On ne relit que les maps rejouées depuis
    la dernière synchronisation, ce qui rend les synchros suivantes quasi instantanées.
    """
    played = api.most_played(user_id)
    known = {r["beatmap_id"]: r["count"] for r in conn.execute("SELECT beatmap_id, count FROM playcounts")}
    changed = []
    for p in played:
        bmap = p.get("beatmap") or {}
        if bmap.get("mode") != "osu":
            continue
        db.upsert_beatmap(conn, bmap, p.get("beatmapset") or {})
        if known.get(p["beatmap_id"]) != p["count"] and bmap.get("status") in SCORED_STATUSES:
            changed.append(p["beatmap_id"])
        conn.execute("INSERT OR REPLACE INTO playcounts VALUES (?, ?)", (p["beatmap_id"], p["count"]))
    conn.commit()
    total_plays = sum(p["count"] for p in played)
    log(f"{len(played)} maps jouées depuis ton inscription ({total_plays} parties), {len(changed)} à relire.")

    last = [0]

    def progress(done: int, total: int) -> None:
        if done == total or done - last[0] >= 50:
            last[0] = done
            log(f"  Scores par map : {done}/{total}")

    results = api.map_parallel(lambda bid: api.user_beatmap_scores(bid, user_id), changed, progress)
    new = 0
    for beatmap_id, scores in zip(changed, results):
        for s in scores:
            s.setdefault("beatmap_id", beatmap_id)
            new += store_score(conn, s, "history")
    conn.commit()
    update_inferred_fails(conn)
    return new


def update_inferred_fails(conn) -> None:
    """Une map classée jouée plusieurs fois sans aucun score enregistré = jamais passée."""
    conn.execute("DELETE FROM scores WHERE source = 'inferred_fail'")
    rows = conn.execute(
        f"""SELECT p.beatmap_id FROM playcounts p JOIN beatmaps b ON b.beatmap_id = p.beatmap_id
            WHERE p.count >= ? AND b.status IN ({",".join("?" * len(SCORED_STATUSES))})
              AND NOT EXISTS (SELECT 1 FROM scores s WHERE s.beatmap_id = p.beatmap_id AND s.passed = 1)""",
        (INFERRED_FAIL_MIN_PLAYS, *SCORED_STATUSES),
    ).fetchall()
    conn.executemany(
        "INSERT OR REPLACE INTO scores VALUES (?, ?, '[]', '', 0, 0, 0, 0, 0, 0, 0, 'F', NULL, ?, 'inferred_fail', 1)",
        [(-r["beatmap_id"], r["beatmap_id"], INFERRED_FAIL_DATE) for r in rows],
    )
    conn.commit()


class WrongPlayer(RuntimeError):
    """La base suit déjà un autre joueur : mélanger deux joueurs fausserait toutes les notes."""


def sync(username: str | None = None, log=print, api=None) -> dict:
    with db.sync_lock():
        return _sync(username, log, api)


def _sync(username: str | None, log, api) -> dict:
    started = time.monotonic()
    conn = db.connect()
    known = db.get_meta(conn, "user")
    username = (username or (known or {}).get("username") or OSU_USERNAME).strip()
    if not username:
        raise ValueError("Indique ton pseudo osu! (OSU_USERNAME dans .env).")
    api = api or OsuApi()
    if db.daily_backup(conn):
        log("Sauvegarde du jour enregistrée dans le dossier « sauvegardes ».")
    before = rewards.snapshot(conn)
    load_bundled_pool(conn, log)
    backfill_hp(conn, log)
    recompute_if_engine_changed(conn, log)
    if reclassify_if_needed(conn):
        removed = progression.refresh_open_tiers(conn)
        if removed:
            log(f"Classement des maps mis à jour : {removed} map(s) de palier remplacée(s).")

    same_name = known and username.lower() == known["username"].lower()
    user = api.user(known["id"] if same_name else username)
    if not user:
        raise ValueError(f"Joueur « {username} » introuvable sur osu!.")
    if known and user["id"] != known["id"]:
        raise WrongPlayer(
            f"Cette base contient les parties de {known['username']}, pas de {user['username']}. "
            "Mélanger deux joueurs fausserait toutes les notes : pour suivre un autre joueur, copie l'outil "
            "dans un autre dossier et mets OSU_DATA_DIR=<un dossier vide> dans son fichier .env."
        )
    stats = user.get("statistics") or {}
    db.set_meta(conn, "user", {
        "id": user["id"],
        "username": user["username"],
        "avatar_url": user.get("avatar_url"),
        "pp": stats.get("pp"),
        "global_rank": stats.get("global_rank"),
        "country_rank": stats.get("country_rank"),
        "accuracy": stats.get("hit_accuracy"),
        "play_count": stats.get("play_count"),
    })
    first_sync = db.get_meta(conn, "tracking_start") is None
    if first_sync:
        db.set_meta(conn, "tracking_start", db.now_iso())

    log(f"Récupération des scores de {user['username']}…")
    best, recent = api.map_parallel(
        lambda kind: api.user_scores(user["id"], kind, include_fails=kind == "recent"), ["best", "recent"]
    )
    new = sum(store_score(conn, s, "recent") for s in recent)
    new += sum(store_score(conn, s, "best") for s in best)
    conn.commit()
    new += import_history(conn, api, user["id"], log)
    try:
        new += lazer.import_local(conn, user["id"], store_score, log)
    except Exception as e:  # la synchro en ligne reste valable même si la lecture locale échoue
        log(f"Lecture des parties locales de lazer impossible : {e}")
    update_inferred_fails(conn)

    rows = conn.execute("SELECT DISTINCT beatmap_id, mods FROM scores WHERE countable = 1").fetchall()
    ensure_attrs_many(conn, [(r["beatmap_id"], json.loads(r["mods"])) for r in rows], log)

    progression.migrate_if_needed(conn, log)
    ratings = rating.recompute(conn)
    clear = rating.clear_level(conn)
    if clear:
        db.set_meta(conn, "clear_level", clear)
    model.update_calibration(conn)
    progression.retire_stuck_tier_maps(conn, log)
    progression.update(conn, api, ratings, log)
    planner.build_session(conn, api, ratings, date.today().isoformat(), log)
    db.set_meta(conn, "last_sync", db.now_iso())
    conn.commit()
    events = rewards.news(before, rewards.snapshot(conn))
    rewards.record_news(conn, events)
    for e in events:
        log(f"{e['title']} : {e['text']}")
    analysed = conn.execute("SELECT count(*) FROM scores WHERE countable = 1").fetchone()[0]
    conn.close()
    log(f"Synchronisation terminée en {time.monotonic() - started:.0f} s ({analysed} parties analysées).")
    return {"new_scores": new, "first_sync": first_sync, "events": events}
