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
