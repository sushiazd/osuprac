"""Vérifie les prévisions de l'outil sur ton historique, jour par jour (chaque jour prédit avec les jours d'avant).

À relancer avant de changer un modèle : un changement n'est gardé que s'il améliore ces chiffres.
Travaille sur une COPIE de la base (jamais d'écriture dans la vraie).

    .venv\\Scripts\\python tools\\backtest.py               (base habituelle)
    .venv\\Scripts\\python tools\\backtest.py chemin\\osu.sqlite

Trois mesures :
1. Les chances affichées dans les séances face à tes premiers essais (et ce qu'apporte la correction des séances).
2. Les modèles de base (finir, finir proprement) sur toutes tes parties.
3. La précision attendue (objectif personnel) sur tes parties finies.
Repères : Brier et log-loss, plus bas = mieux ; AUC, 0,5 = hasard, 1 = parfait.
"""
import json
import math
import shutil
import sqlite3
import sys
import tempfile
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from osuprog import db, goals, model, rating  # noqa: E402
from osuprog.config import DB_PATH  # noqa: E402
from osuprog.mods import EASY_MODS  # noqa: E402
from osuprog.skills import CLEAN_FINISH, meets  # noqa: E402

START_DAYS = 14  # jours d'historique avant de commencer à juger


def copy_db(src: Path) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="osu-backtest-")) / "osu.sqlite"
    s = sqlite3.connect(f"file:{src.as_posix()}?mode=ro&immutable=1", uri=True)
    d = sqlite3.connect(tmp)
    s.backup(d)
    s.close()
    d.close()
    return tmp


def scores(ps, os_):
    n = len(ps)
    brier = sum((p - o) ** 2 for p, o in zip(ps, os_)) / n
    ll = sum(-(o * math.log(min(.999, max(.001, p))) + (1 - o) * math.log(1 - min(.999, max(.001, p))))
             for p, o in zip(ps, os_)) / n
    pos = [p for p, o in zip(ps, os_) if o]
    neg = [p for p, o in zip(ps, os_) if not o]
    auc = (sum((a > b) + 0.5 * (a == b) for a in pos for b in neg) / (len(pos) * len(neg))) if pos and neg else float("nan")
    return f"n={n:4d} Brier {brier:.3f} log-loss {ll:.3f} AUC {auc:.2f} | prévu {sum(ps) / n:4.0%} réel {sum(os_) / n:4.0%}"


def session_chances(conn):
    print("\n1. CHANCES AFFICHÉES DANS LES SÉANCES (premier essai du jour)")
    att = list(reversed(goals.first_attempts(conn)))
    if len(att) < 10:
        print("   pas assez de maps du programme jouées")
        return
    ps, os_ = [a["p"] for a in att], [1.0 if a["met"] else 0.0 for a in att]
    print("   telles qu'affichées     ", scores(ps, os_))
    base = sum(os_) / len(os_)
    print("   repère (toujours", f"{base:.0%})", scores([base] * len(os_), os_))
    recal = []
    for k, a in enumerate(att):  # correction apprise sur les jours d'avant seulement
        past = [b for b in att[:k] if b["day"] < a["day"]][-model.SESSION_OFFSET_RECENT:]
        d = model.fit_offset([(model.logit(b["p_raw"]), 1.0 if b["met"] else 0.0, 1.0) for b in past]) if past else 0.0
        recal.append(model.corrected(a["p_raw"], d))
    print("   avec correction rejouée ", scores(recal, os_))
    by_role = defaultdict(list)
    for a, o in zip(att, os_):
        by_role[a["role"]].append((a["p"], o))
    for role, v in sorted(by_role.items(), key=lambda x: -len(x[1])):
        if len(v) >= 5:
            print(f"   {role:<24}", scores([p for p, _ in v], [o for _, o in v]))


def plays(conn):
    rows = conn.execute(
        """SELECT s.beatmap_id, s.mods, s.mods_key, s.passed, s.accuracy, s.n_miss, s.ended_at, a.stars, a.attrs
           FROM scores s JOIN map_attrs a ON a.beatmap_id = s.beatmap_id AND a.mods_key = s.mods_key
           WHERE s.countable = 1 AND NOT s.abandoned AND s.source IN ('recent', 'lazer_local') ORDER BY s.ended_at""").fetchall()
    out = []
    for r in rows:
        if {m["acronym"] for m in json.loads(r["mods"])} & EASY_MODS:
            continue
        a = json.loads(r["attrs"])
        out.append({"bid": r["beatmap_id"], "key": r["mods_key"], "day": db.local_day(r["ended_at"]), "stars": r["stars"],
                    "hp": a.get("hp", 4.0), "od": a.get("od"), "length": a.get("length_s"), "passed": bool(r["passed"]),
                    "acc": r["accuracy"],
                    "clean": meets(bool(r["passed"]), r["accuracy"], r["n_miss"], a.get("n_objects") or 400, CLEAN_FINISH)})
    return out


def base_models(ps_all):
    print("\n2. MODÈLES DE BASE, TOUTES TES PARTIES (jour par jour)")
    days = sorted({p["day"] for p in ps_all})
    res = {"passed": ([], []), "clean": ([], [])}
    for d in days[START_DAYS:]:
        train = [p for p in ps_all if p["day"] < d]
        test = [p for p in ps_all if p["day"] == d]
        last = date.fromisoformat(max(p["day"] for p in train))
        for t in res:
            pts = {}
            for p in train:
                k = (p["bid"], p["key"], p["day"])
                pts[k] = (p["stars"], p["hp"], 1.0 if (p[t] or (k in pts and pts[k][2])) else 0.0)
            fit = rating.fit_clear_level(list(pts.values()), [0.5 ** ((last - date.fromisoformat(k[2])).days
                                                                        / rating.CLEAR_HALF_LIFE_DAYS) for k in pts])
            if not fit:
                continue
            for p in test:
                res[t][0].append(model.finish_prob(p["stars"], p["hp"], fit["level"], fit["scale"], fit["hp_k"]))
                res[t][1].append(1.0 if p[t] else 0.0)
    for t, label in (("passed", "finir"), ("clean", "finir proprement")):
        ps, os_ = res[t]
        if ps:
            print(f"   {label:<24}", scores(ps, os_))
            zone = [(p, o) for p, o in zip(ps, os_) if 0.3 < p < 0.8]
            if zone:
                print(f"   … zone 30-80 %          ", scores([p for p, _ in zone], [o for _, o in zone]))


def acc_model(conn, ps_all):
    print("\n3. PRÉCISION ATTENDUE SUR TES PARTIES FINIES (jour par jour)")
    days = sorted({p["day"] for p in ps_all})
    errors, hits = [], []
    for d in days[START_DAYS:]:
        m = rating.fit_acc_model(conn, before=f"{d}T00:00:00Z")  # réappris sur les jours d'avant
        if not m:
            continue
        for p in ps_all:
            if p["day"] != d or not p["passed"]:
                continue
            exp = model.expected_acc(m, p["stars"], p["hp"], p["od"], p["length"], p["bid"])
            errors.append(abs(p["acc"] - exp))
            hits.append((model.p_acc_at_least(m, model.acc_goal(m, exp), exp), 1.0 if p["acc"] >= model.acc_goal(m, exp) else 0.0))
    if errors:
        errors.sort()
        print(f"   erreur moyenne {sum(errors) / len(errors):.1f} pts | 80 % des parties à ±{errors[int(.8 * len(errors))]:.1f} pts")
        print("   objectif personnel tenu", scores([h[0] for h in hits], [h[1] for h in hits]))


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DB_PATH
    path = copy_db(src)
    conn = db.connect(path)
    print(f"Base : {src} (copie de travail : {path})")
    session_chances(conn)
    ps_all = plays(conn)
    base_models(ps_all)
    acc_model(conn, ps_all)
    conn.close()
    shutil.rmtree(path.parent, ignore_errors=True)


if __name__ == "__main__":
    main()
