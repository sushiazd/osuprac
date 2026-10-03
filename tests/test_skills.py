from osuprog.osufile import pattern_features
from osuprog.skills import CATEGORIES, Criterion, classify, meets, outcome

CRIT = Criterion(80, 2)


def test_max_miss_scales_with_notes_and_allows_at_least_one():
    assert CRIT.max_miss(1000) == 20
    assert CRIT.max_miss(10) == 1


def test_meets_requires_pass_accuracy_and_misses():
    assert meets(True, 80.0, 6, 300, CRIT)
    assert not meets(False, 99.0, 0, 300, CRIT)
    assert not meets(True, 79.9, 0, 300, CRIT)
    assert not meets(True, 95.0, 7, 300, CRIT)


def test_outcome_is_graded_between_zero_and_one():
    assert outcome(False, 99, 0, 300, CRIT) == 0.0
    assert outcome(True, 85, 0, 300, CRIT) == 1.0
    assert outcome(True, 75, 0, 300, CRIT) == 0.5      # 5 points sous l'objectif, rampe de 10 points
    assert outcome(True, 70, 0, 300, CRIT) == 0.0
    partial = outcome(True, 90, 10, 300, CRIT)
    assert 0 < partial < 1


def _attrs(**kw):
    base = {"stars": 3.0, "aim": 1.5, "speed": 1.2, "slider_factor": 0.985, "od": 6.0, "ar": 7.5, "hp": 4,
            "stream_frac": 0.0, "long_stream_frac": 0.0, "max_run": 0, "length_s": 120.0, "hidden": False}
    return {**base, **kw}


def test_classify_returns_normalised_weights():
    w = classify(_attrs())
    assert set(w) <= set(CATEGORIES)
    assert abs(sum(w.values()) - 1) < 0.01


def test_classify_recognises_typical_profiles():
    assert max(classify(_attrs(stream_frac=0.4, max_run=9)), key=classify(_attrs(stream_frac=0.4, max_run=9)).get) == "speed"
    acc = classify(_attrs(od=8.5))
    assert acc.get("accuracy", 0) >= 0.3
    long_streams = classify(_attrs(stream_frac=0.5, long_stream_frac=0.3, max_run=40, length_s=240))
    assert long_streams.get("stamina", 0) >= 0.3
    low_ar = classify(_attrs(ar=5.0))
    assert low_ar.get("reading", 0) >= 0.3


def _osu(objects):
    lines = ["osu file format v14", "", "[HitObjects]"]
    lines += [f"256,192,{t},{kind},0" for t, kind in objects]
    return "\n".join(lines).encode()


def test_pattern_features_finds_streams_and_ignores_triplets():
    # 3 notes rapprochées (triolet), pause, puis un stream de 12 notes à 1/4 de 180 BPM (83 ms).
    objs = [(0, 1), (83, 1), (166, 1), (1000, 1)] + [(2000 + 83 * i, 1) for i in range(12)]
    f = pattern_features(_osu(objs))
    assert f["max_run"] == 12
    assert f["n_objects"] == 16
    assert abs(f["stream_bpm"] - 180) < 2
    assert f["stream_frac"] == round(12 / 16, 3)


def test_pattern_features_respects_clock_rate():
    objs = [(i * 150, 1) for i in range(10)]  # 150 ms : pas un stream à vitesse normale
    assert pattern_features(_osu(objs))["max_run"] == 0
    assert pattern_features(_osu(objs), clock_rate=1.5)["max_run"] == 10  # 100 ms en DT
