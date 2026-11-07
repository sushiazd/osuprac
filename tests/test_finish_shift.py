"""Le niveau de passe se recale sur ce que montrent les maps nouvelles du programme."""
from datetime import date, datetime, timedelta, timezone

from conftest import add_map, score

from osuprog import db, model
from osuprog.sync import store_score

CLEAR = {"level": 3.0, "scale": 0.5, "hp_k": 0.0, "plays": 100, "weight": 100.0}


def planned_first_play(conn, beatmap_id, days_ago, passed, stars=3.0, replay_before=False):
    add_map(conn, beatmap_id, stars=stars)
    day = (date.today() - timedelta(days=days_ago))
    when = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
    if replay_before:
        store_score(conn, score(beatmap_id * 10, beatmap_id, (when - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")),
                    "recent")
    store_score(conn, score(beatmap_id * 10 + 1, beatmap_id, when.strftime("%Y-%m-%dT%H:%M:%SZ"), passed=passed,
                                           miss=0 if passed else 60),
                "recent")
    conn.execute("INSERT INTO session_plan (day, pos, role, category, level_c, beatmap_id, p) "
                 "VALUES (?, ?, 'Entraînement', 'aim', 300, ?, 0.5)", (day.isoformat(), beatmap_id, beatmap_id))
    conn.commit()


def test_no_data_no_shift(conn):
    assert model.finish_shift(conn) == 0.0
    db.set_meta(conn, "clear_level", CLEAR)
    assert model.finish_shift(conn) == 0.0


def test_finishing_more_maps_than_predicted_raises_the_level_prudently(conn):
    db.set_meta(conn, "clear_level", CLEAR)
    for i in range(1, 21):   # 3,0★ pour un niveau de 3,0★ : 50 % prévus, 20 finies sur 20
        planned_first_play(conn, i, days_ago=i % 10, passed=True)
    shift = model.finish_shift(conn)
    assert 0.1 < shift <= model.FINISH_SHIFT_RANGE[1]


def test_failing_more_than_predicted_lowers_it(conn):
    db.set_meta(conn, "clear_level", CLEAR)
    for i in range(1, 21):
        planned_first_play(conn, i, days_ago=i % 10, passed=False)
    assert model.finish_shift(conn) < 0


def test_maps_already_played_before_do_not_count(conn):
    db.set_meta(conn, "clear_level", CLEAR)
    for i in range(1, 21):
        planned_first_play(conn, i, days_ago=i % 10, passed=True, replay_before=True)
    assert model.finish_shift(conn) == 0.0


def test_shifted_level_is_a_copy():
    assert model.shifted(CLEAR, 0.2)["level"] == 3.2 and CLEAR["level"] == 3.0
    assert model.shifted(None, 0.2) is None and model.shifted(CLEAR, 0) is CLEAR
