import math
import random

from osuprog import model
from osuprog.rating import expected, fit_clear_level


def test_expected_is_one_half_at_own_level_and_monotonic():
    assert expected(3.0, 3.0) == 0.5
    assert expected(3.0, 2.5) > 0.5 > expected(3.0, 3.5)


def test_fit_clear_level_recovers_the_true_level():
    rng = random.Random(1)
    points = []
    for _ in range(600):
        stars = rng.uniform(2.0, 5.0)
        p = 1 / (1 + math.exp(-(3.6 - stars) / 0.5))
        points.append((stars, 1.0 if rng.random() < p else 0.0))
    fit = fit_clear_level(points)
    assert abs(fit["level"] - 3.6) < 0.15


def test_fit_clear_level_needs_both_outcomes():
    assert fit_clear_level([(3.0, 1.0)] * 30) is None
    assert fit_clear_level([(3.0, 1.0), (4.0, 0.0)]) is None  # trop peu de parties


def test_finish_prob_drops_with_high_hp():
    clear = {"level": 3.5, "scale": 0.5, "hp_k": 0.3}
    assert model.finish_prob_for(clear, 3.5, 4.0) == 0.5
    assert model.finish_prob_for(clear, 3.5, 7.0) < 0.2
    assert model.finish_prob_for(None, 3.5, 4.0) is None


def test_skill_gaps_flags_only_differences_beyond_the_margin():
    games = {s: 100.0 for s in "abcdef"}
    ratings = {"a": 3.0, "b": 3.05, "c": 2.95, "d": 3.0, "e": 3.0, "f": 2.6}
    gaps = model.skill_gaps(ratings, games)
    assert gaps["f"]["significant"] and gaps["f"]["gap"] < 0
    assert not gaps["b"]["significant"]
    few = model.skill_gaps(ratings, {s: 1.0 for s in "abcdef"})  # notes très incertaines
    assert not few["f"]["significant"]


def test_strongest_skill_ignores_unmeasured_ones():
    assert model.strongest_skill({"a": 3.0, "b": 5.0}, {"a": 10, "b": 1}) == "a"


def test_calibration_corrects_an_overconfident_model():
    rng = random.Random(2)
    samples = []
    for _ in range(2000):
        z = rng.uniform(-3, 3)
        truth = model.sigmoid(-0.5 + 0.8 * z)  # le joueur réussit moins que prévu
        samples.append((z, 1.0 if rng.random() < truth else 0.0, 1.0))
    a, b = model.fit_calibration(samples, prior=1.0)
    assert -0.7 < a < -0.3 and 0.65 < b < 0.95


def test_clear_level_counts_a_replayed_map_once_per_day(conn):
    """40 relances ratées d'une même map ne doivent pas tirer le niveau de réussite vers le bas."""
    from conftest import add_map, score
    from osuprog.rating import clear_level
    from osuprog.sync import store_score

    sid = 1
    for i in range(30):  # 30 maps de 3 à 6★ : finies jusqu'à 5★, ratées au-delà
        stars = 3.0 + i * 0.1
        add_map(conn, 100 + i, stars=stars)
        store_score(conn, score(sid, 100 + i, f"2026-10-{1 + i % 25:02d}T12:00:00Z", passed=stars <= 4.5, miss=0 if stars <= 4.5 else 25), "recent")
        sid += 1
    normal = clear_level(conn)["level"]
    add_map(conn, 999, stars=3.5)
    for k in range(40):  # même map, même jour, 40 échecs (relances)
        store_score(conn, score(sid, 999, f"2026-10-20T12:{k:02d}:00Z", accuracy=0.6, miss=30, passed=False), "recent")
        sid += 1
    assert abs(clear_level(conn)["level"] - normal) < 0.15


def test_clear_level_is_pulled_toward_skill_ratings_when_data_is_flat(conn):
    """Peu de parties : le niveau de réussite ne reste pas à 2★ pour un joueur de 5★."""
    from conftest import add_map, score
    from osuprog.rating import clear_level
    from osuprog.sync import store_score

    conn.executemany("INSERT OR REPLACE INTO ratings (category, rating, games, level) VALUES (?, 5.4, 50, 5.4)",
                     [("aim",), ("speed",)])
    for i in range(25):  # 25 parties seulement, réussies jusqu'à 3,5★
        add_map(conn, 200 + i, stars=2.0 + i * 0.3)
        store_score(conn, score(i + 1, 200 + i, f"2026-10-{1 + i:02d}T12:00:00Z", passed=2.0 + i * 0.3 <= 3.5,
                                miss=0 if 2.0 + i * 0.3 <= 3.5 else 25), "recent")
    assert clear_level(conn)["level"] >= 4.5
