"""Version 2 du programme : correction des chances apprise sur les séances, objectif personnel de précision,
projets, et temps de séance compté sur les seules maps du programme."""
from datetime import date, datetime, timedelta, timezone

from conftest import add_map, score
from test_planner import candidates, context

from osuprog import db, goals, model, planner, projects, rating
from osuprog.planner import Cand, plan
from osuprog.sync import store_score

TODAY = date.today()


def when(days_ago: int, minute: int = 0) -> str:
    d = TODAY - timedelta(days=days_ago)
    return datetime(d.year, d.month, d.day, 12, minute, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def planned(conn, beatmap_id, days_ago, *, passed=True, accuracy=0.9, p_raw=0.5, role="Entraînement", goal_acc=None,
            minutes=4.0):
    add_map(conn, beatmap_id)
    store_score(conn, score(beatmap_id * 10, beatmap_id, when(days_ago), accuracy=accuracy, passed=passed,
                            miss=0 if passed else 60), "recent")
    day = (TODAY - timedelta(days=days_ago)).isoformat()
    pos = conn.execute("SELECT count(*) FROM session_plan WHERE day = ?", (day,)).fetchone()[0]
    conn.execute("INSERT INTO session_plan (day, pos, role, category, level_c, beatmap_id, p, p_raw, goal_acc, minutes) "
                 "VALUES (?, ?, ?, 'aim', 300, ?, ?, ?, ?, ?)", (day, pos, role, beatmap_id, p_raw, p_raw, goal_acc,
                                                                 minutes))
    conn.commit()


# --- Buts --------------------------------------------------------------------------------------------------------

def test_each_role_is_judged_on_its_own_goal():
    assert goals.kind("Défi") == "finir" and goals.kind("Montée") == "propre"
    assert goals.kind("Projet", "propre") == "propre" and goals.kind("Projet") == "finir"
    assert goals.kind("Entraînement", goal_acc=78) == "acc"
    assert goals.kind("Entraînement") == "critère"  # anciennes séances, sans objectif personnel
    assert goals.achieved("acc", "aim", True, 78.0, 3, 300, 78)
    assert not goals.achieved("acc", "aim", True, 77.9, 0, 300, 78)
    assert not goals.achieved("acc", "aim", False, 95.0, 0, 300, 78)
    assert goals.achieved("propre", "aim", True, 60.0, 24, 300) and not goals.achieved("propre", "aim", True, 99, 25, 300)


# --- Correction des chances ----------------------------------------------------------------------------------------

def test_no_session_no_correction(conn):
    assert goals.update_session_offset(conn) == {"d": 0.0, "n": 0}
    assert model.load_offset(conn) == 0.0
    assert model.corrected(0.42, 0.0) == 0.42 and model.corrected(None, 1.0) is None


def test_sessions_more_successful_than_shown_raise_the_chances(conn):
    for i in range(30):
        planned(conn, 100 + i, days_ago=1 + i % 10, passed=True, accuracy=0.95, p_raw=0.5)
    out = goals.update_session_offset(conn)
    assert out["n"] == 30 and 0.3 < out["d"] <= model.SESSION_OFFSET_MAX
    assert model.corrected(0.5, model.load_offset(conn)) > 0.6


def test_the_correction_learns_from_raw_chances_not_its_own_output(conn):
    for i in range(20):
        planned(conn, 200 + i, days_ago=1 + i % 5, passed=i % 2 == 0, accuracy=0.95, p_raw=0.5)
    conn.execute("UPDATE session_plan SET p = 0.9")  # chances affichées déjà corrigées : ignorées pour l'apprentissage
    conn.commit()
    assert abs(goals.update_session_offset(conn)["d"]) < 0.1


def test_personal_accuracy_goal_is_judged_on_the_first_attempt(conn):
    planned(conn, 300, days_ago=1, passed=True, accuracy=0.80, goal_acc=78)
    planned(conn, 301, days_ago=1, passed=True, accuracy=0.75, goal_acc=78)
    met = {a["p"]: a["met"] for a in goals.first_attempts(conn)}
    assert sorted(a["met"] for a in goals.first_attempts(conn)) == [False, True] and met


# --- Précision habituelle ------------------------------------------------------------------------------------------

def test_accuracy_model_expects_less_on_harder_maps(conn):
    for i in range(60):
        stars = 3.0 + (i % 20) * 0.1
        add_map(conn, 400 + i, stars=stars)
        store_score(conn, score(4000 + i, 400 + i, when(i % 15), accuracy=(92 - 6 * (stars - 3)) / 100), "recent")
    m = rating.fit_acc_model(conn)
    assert m and m["plays"] == 60
    easy, hard = model.expected_acc(m, 3.0, 4, 8, 120), model.expected_acc(m, 4.5, 4, 8, 120)
    assert easy > hard + 5
    goal = model.acc_goal(m, hard)
    assert goal <= hard + 1
    assert model.p_acc_at_least(m, goal - 5, hard) > model.p_acc_at_least(m, goal + 5, hard)


def test_absurd_overall_difficulty_does_not_promise_absurd_accuracy():
    m = {"coef": [84.0, -3.2, 0.8, -3.0, 2.3], "quantiles": [0.0] * 19, "map": {}}
    assert model.expected_acc(m, 5.0, 4, 1.0, 120) == model.expected_acc(m, 5.0, 4, 5.0, 120)


# --- Projets -------------------------------------------------------------------------------------------------------

def grind(conn, beatmap_id, plays, stars=5.0):
    add_map(conn, beatmap_id, stars=stars, n_objects=400)
    for k, (days_ago, passed, miss) in enumerate(plays):
        s = score(beatmap_id * 100 + k, beatmap_id, when(days_ago, k), accuracy=0.8, passed=passed, miss=miss)
        if not passed:
            s["statistics"] = {"great": 100 + 20 * k, "ok": 0, "meh": 0, "miss": miss}
        store_score(conn, s, "recent")


def test_a_map_you_keep_retrying_becomes_a_project(conn):
    grind(conn, 900, [(3, False, 10), (3, False, 12), (2, False, 9), (2, False, 8), (1, False, 7)])
    grind(conn, 901, [(1, False, 10)] * 6)  # une seule soirée de relances : pas un projet
    active = projects.active(conn)
    assert [p["beatmap_id"] for p in active] == [900]
    assert active[0]["stage"] == "finir" and len(active[0]["days"]) == 3
    assert 0 < active[0]["days"][-1]["reach"] < 1


def test_a_project_moves_to_clean_finish_then_can_be_dismissed(conn):
    grind(conn, 902, [(3, False, 10), (3, False, 12), (2, True, 40), (1, False, 9), (1, False, 8)])
    assert projects.active(conn)[0]["stage"] == "propre"
    projects.dismiss(conn, 902)
    assert projects.active(conn) == []


def test_project_day_has_preparation_then_the_project_and_no_challenge():
    cands = candidates()
    style = {"aim": 2.0, "speed": 1.5, "ar": 9, "cs": 4, "od": 8, "length_s": 120, "n_objects": 400, "hp": 4.0}
    for c in cands:
        c.attrs = dict(style, aim=2.0 + (c.beatmap_id % 7) * 0.05, length_s=c.length_s)
    proj = Cand(9999, 9999, "someone", 4.2, {"aim": 0.8, "speed": 0.2}, 120.0, attrs=dict(style))
    items, _ = plan(context(level=3.8, clear_level=3.8, project={"cand": proj, "stage": "finir", "p": 0.3}), cands)
    roles = [i.role for i in items]
    assert "Défi" not in roles and roles.count("Projet") == 1
    assert 1 <= roles.count("Préparation") <= planner.PREP_MAX
    assert roles.index("Préparation") < roles.index("Projet")
    proj_item = next(i for i in items if i.role == "Projet")
    assert proj_item.beatmap_id == 9999 and proj_item.goal == "finir" and proj_item.p == 0.3


# --- Objectif personnel dans le programme -----------------------------------------------------------------------

def test_training_maps_carry_a_personal_accuracy_goal_when_the_model_exists():
    m = {"coef": [84.0, -3.2, 0.8, -3.0, 2.3], "quantiles": [-12 + k for k in range(19)], "map": {}}
    items, _ = plan(context(acc_model=m), candidates())
    training = [i for i in items if i.role == "Entraînement"]
    assert training and all(i.goal_acc is not None and 50 <= i.goal_acc <= 99 for i in training)
    assert all(i.p_raw is not None for i in items)


def test_the_session_correction_raises_shown_chances():
    a, _ = plan(context(offset=0.0), candidates())
    b, _ = plan(context(offset=0.8), candidates())
    assert sum(i.p for i in b) / len(b) > sum(i.p for i in a) / len(a)


# --- Temps de séance -----------------------------------------------------------------------------------------------

def test_only_program_maps_use_up_the_session(conn):
    day = TODAY.isoformat()
    planned(conn, 700, days_ago=0, minutes=4.0)
    add_map(conn, 701)
    for k in range(6):  # six maps hors programme, puis une longue pause : la séance n'est pas vidée
        store_score(conn, score(7010 + k, 701, when(0, k)), "recent")
    assert planner.program_minutes_played(conn, day) == 4.0
