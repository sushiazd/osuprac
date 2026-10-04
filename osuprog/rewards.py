"""Récompenses : série de jours joués, succès débloqués et nouveautés à annoncer après une synchronisation.

Tout est recalculé depuis les parties en base : rien à stocker, et un succès ne peut pas être « perdu ».
"""
import json
from dataclasses import dataclass
from datetime import date, timedelta

from . import db, fmt
from .mods import EASY_MODS
from .skills import LABELS

NEWS_KEPT = 50


@dataclass
class Achievement:
    id: str
    group: str
    title: str
    text: str
    icon: str
    unlocked: str | None = None      # jour local du déblocage
    progress: float = 0.0            # 0 à 1 tant qu'il n'est pas débloqué
    level: int = 0                   # rang dans son échelle (0 = premier palier) : sert à la couleur du badge


# --- Série de jours -----------------------------------------------------------------------------

def play_days(conn) -> list[str]:
    rows = conn.execute("SELECT ended_at FROM scores WHERE source != 'inferred_fail'").fetchall()
    return sorted({db.local_day(r["ended_at"]) for r in rows})


def streaks(days: list[str], today: str) -> dict:
    """Série en cours (toujours vivante si tu as joué hier mais pas encore aujourd'hui) et meilleure série."""
    ds = sorted({date.fromisoformat(d) for d in days})
    best = run = 0
    prev = None
    for d in ds:
        run = run + 1 if prev and d - prev == timedelta(days=1) else 1
        best = max(best, run)
        prev = d
    t = date.fromisoformat(today)
    alive = bool(ds) and ds[-1] >= t - timedelta(days=1)
    return {"current": run if alive else 0, "best": best, "played_today": bool(ds) and ds[-1] == t}


# --- Succès ---------------------------------------------------------------------------------------

def _plays(conn) -> list[dict]:
    rows = conn.execute(
        """SELECT s.ended_at, s.accuracy, s.n_miss, s.passed, s.mods, a.stars FROM scores s
           JOIN map_attrs a ON a.beatmap_id = s.beatmap_id AND a.mods_key = s.mods_key
           WHERE s.countable = 1 AND s.source != 'inferred_fail' ORDER BY s.ended_at"""
    ).fetchall()
    out = []
    for r in rows:
        if {m["acronym"] for m in json.loads(r["mods"])} & EASY_MODS:
            continue
        out.append({**dict(r), "day": db.local_day(r["ended_at"])})
    return out


def _first(plays, cond) -> str | None:
    return next((p["day"] for p in plays if cond(p)), None)


def _nth_day(days: list[str], n: int) -> str | None:
    return days[n - 1] if len(days) >= n else None


def _ladder(group, icon, steps, make, value, when) -> list[Achievement]:
    """Une échelle de succès : tous ceux débloqués, puis le prochain (avec sa progression)."""
    out = []
    for level, step in enumerate(steps):
        title, text = make(step)
        a = Achievement(f"{group}:{step}", group, title, text, icon, when(step), level=level)
        if a.unlocked is None:
            a.progress = max(0.0, min(0.99, value / step)) if isinstance(step, (int, float)) and value else 0.0
            out.append(a)
            break
        out.append(a)
    return out


def achievements(conn, today: str | None = None) -> list[Achievement]:
    today = today or date.today().isoformat()
    plays = _plays(conn)
    passed = [p for p in plays if p["passed"]]
    out: list[Achievement] = []

    best_stars = max((p["stars"] for p in passed), default=0.0)
    out += _ladder("clear", ":material/star:", [2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0],
                   lambda x: (f"Première {fmt.num(x)}★", f"Finir une map de {fmt.stars(x)} ou plus"),
                   best_stars, lambda x: _first(passed, lambda p: p["stars"] >= x))

    acc_steps = [(85, 3.0), (90, 3.0), (85, 3.5), (95, 3.0), (90, 3.5), (85, 4.0), (90, 4.0), (95, 4.0)]
    for level, (acc, x) in enumerate(acc_steps):
        out.append(Achievement(f"acc:{acc}:{x}", "acc", f"{acc} % à {fmt.num(x)}★",
                               f"Finir une map de {fmt.stars(x)} ou plus avec au moins {acc} % de précision",
                               ":material/adjust:",
                               _first(passed, lambda p, acc=acc, x=x: p["stars"] >= x and p["accuracy"] >= acc),
                               level=level))
    locked_acc = [a for a in out if a.group == "acc" and not a.unlocked]
    out = [a for a in out if a.group != "acc" or a.unlocked] + locked_acc[:2]

    out += _ladder("nomiss", ":material/verified:", [2.5, 3.0, 3.5, 4.0, 4.5, 5.0],
                   lambda x: (f"Zéro miss à {fmt.num(x)}★", f"Finir une map de {fmt.stars(x)} ou plus sans aucun miss"),
                   max((p["stars"] for p in passed if p["n_miss"] == 0), default=0.0),
                   lambda x: _first(passed, lambda p: p["stars"] >= x and p["n_miss"] == 0))

    tiers = conn.execute("SELECT at FROM tier_log WHERE category != 'exam' ORDER BY at").fetchall()
    tier_days = [db.local_day(r["at"]) for r in tiers]
    out += _ladder("tiers", ":material/flag:", [1, 5, 10, 20, 40, 80],
                   lambda n: (f"{n} palier{'s' if n > 1 else ''}", f"Valider {n} palier{'s' if n > 1 else ''}"),
                   len(tier_days), lambda n: _nth_day(tier_days, n))

    for r in conn.execute("SELECT level_c, at FROM tier_log WHERE category = 'exam' ORDER BY level_c"):
        out.append(Achievement(f"rank:{r['level_c']}", "rank", f"Niveau {fmt.level(r['level_c'])}",
                               f"Réussir l'examen du niveau {fmt.level(r['level_c'])}", ":material/school:",
                               db.local_day(r["at"]), level=4))

    days = play_days(conn)
    st = streaks(days, today)
    out += _ladder("streak", ":material/local_fire_department:", [3, 7, 14, 30, 60, 100],
                   lambda n: (f"{n} jours d'affilée", f"Jouer {n} jours de suite"),
                   st["best"], lambda n: _streak_day(days, n))

    play_list = [p["day"] for p in plays]
    out += _ladder("plays", ":material/sports_esports:", [100, 250, 500, 1000, 2500, 5000],
                   lambda n: (f"{n} parties", f"Jouer {n} parties"),
                   len(play_list), lambda n: _nth_day(play_list, n))

    sessions = complete_sessions(conn)
    out += _ladder("sessions", ":material/task_alt:", [1, 5, 10, 25, 50],
                   lambda n: (f"{n} séance{'s' if n > 1 else ''} complète{'s' if n > 1 else ''}",
                              f"Jouer toutes les maps du programme du jour, {n} fois"),
                   len(sessions), lambda n: _nth_day(sessions, n))
    return out


def _streak_day(days: list[str], n: int) -> str | None:
    """Jour où une série de n jours a été atteinte pour la première fois."""
    run, prev = 0, None
    for d in sorted(date.fromisoformat(x) for x in days):
        run = run + 1 if prev and d - prev == timedelta(days=1) else 1
        prev = d
        if run >= n:
            return d.isoformat()
    return None


def complete_sessions(conn) -> list[str]:
    """Jours où toutes les maps du programme ont été jouées."""
    out = []
    for r in conn.execute("SELECT DISTINCT day FROM session_plan ORDER BY day"):
        ids = [x["beatmap_id"] for x in conn.execute("SELECT beatmap_id FROM session_plan WHERE day = ?", (r["day"],))]
        played = {x["beatmap_id"] for x in conn.execute(
            f"SELECT beatmap_id, ended_at FROM scores WHERE source != 'inferred_fail' AND beatmap_id IN "
            f"({','.join('?' * len(ids))})", ids) if db.local_day(x["ended_at"]) == r["day"]}
        if ids and set(ids) <= played:
            out.append(r["day"])
    return out


# --- Nouveautés -----------------------------------------------------------------------------------

def snapshot(conn) -> dict:
    return {
        "plays": conn.execute("SELECT count(*) FROM scores WHERE source != 'inferred_fail'").fetchone()[0],
        "tiers": {(r["category"], r["level_c"]) for r in conn.execute("SELECT category, level_c FROM tier_log")},
        "achievements": {a.id: a for a in achievements(conn) if a.unlocked},
    }


def news(before: dict, after: dict) -> list[dict]:
    """Ce qui a changé pendant la synchronisation. Rien à la toute première (tout serait « nouveau »)."""
    if before["plays"] == 0:
        return []
    out = []
    for cat, level_c in sorted(after["tiers"] - before["tiers"]):
        if cat == "exam":
            out.append({"kind": "rank", "title": "Niveau validé !", "text": f"Tu passes au niveau {fmt.level(level_c)}."})
        else:
            out.append({"kind": "tier", "title": "Palier validé", "text": f"{LABELS[cat]} {fmt.level(level_c)}"})
    for aid in sorted(set(after["achievements"]) - set(before["achievements"])):
        a = after["achievements"][aid]
        if a.group == "rank":
            continue  # déjà annoncé comme niveau validé
        out.append({"kind": "achievement", "title": "Succès débloqué", "text": f"{a.title} : {a.text.lower()}"})
    return out


def record_news(conn, events: list[dict]) -> None:
    """Garde les nouveautés pour les montrer à la prochaine ouverture du tableau de bord."""
    if not events:
        return
    stored = db.get_meta(conn, "news", [])
    stored += [{**e, "at": db.now_iso()} for e in events]
    db.set_meta(conn, "news", stored[-NEWS_KEPT:])
    conn.commit()


def unseen_news(conn) -> list[dict]:
    seen = db.get_meta(conn, "news_seen_at", "")
    return [e for e in db.get_meta(conn, "news", []) if e["at"] > seen]


def mark_news_seen(conn) -> None:
    db.set_meta(conn, "news_seen_at", db.now_iso())
    conn.commit()


# --- Bilan de la semaine ------------------------------------------------------------------------------

def week_summary(conn, today: str, days: int = 7) -> dict:
    """Ce que tu as fait ces `days` derniers jours (aujourd'hui compris), comparé à la période d'avant."""
    from .planner import _minutes_played_today  # import tardif : planner importe beaucoup de modules
    t = date.fromisoformat(today)
    window = {(t - timedelta(days=i)).isoformat() for i in range(days)}
    plays = [p for p in _plays(conn) if p["day"] in window]
    start = min(window)
    rating_now = {r["category"]: r["rating"] for r in conn.execute("SELECT category, rating FROM ratings")}
    rating_then = {}
    for r in conn.execute(
            """SELECT e.category, e.rating_after, s.ended_at FROM rating_events e JOIN scores s ON s.score_id = e.score_id
               WHERE s.source != 'inferred_fail' ORDER BY s.ended_at"""):
        if db.local_day(r["ended_at"]) < start:
            rating_then[r["category"]] = r["rating_after"]
    tiers = [r for r in conn.execute("SELECT category, level_c, at FROM tier_log WHERE category != 'exam'")
             if db.local_day(r["at"]) in window]
    passed = [p for p in plays if p["passed"]]
    return {
        "plays": len(plays),
        "passed": len(passed),
        "days_played": len({p["day"] for p in plays}),
        "minutes": round(sum(_minutes_played_today(conn, d) for d in window)),
        "tiers": len(tiers),
        "hardest": max((p["stars"] for p in passed), default=None),
        # Sur une map à ton niveau (au moins ta note la plus basse) : 99 % sur une map facile ne dit rien.
        "best_acc": max((p["accuracy"] for p in passed if p["stars"] >= min(rating_now.values(), default=0)),
                        default=None),
        "level": min(rating_now.values(), default=None),
        "rating_change": {c: rating_now[c] - rating_then[c] for c in rating_now if c in rating_then},
    }
