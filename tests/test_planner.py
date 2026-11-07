from osuprog import planner
from osuprog.planner import Cand, Context, plan, skill_shares
from osuprog.skills import CATEGORIES

SKILL_OF = {i: CATEGORIES[i % len(CATEGORIES)] for i in range(400)}


def candidates(n=300):
    out = []
    for i in range(n):
        main = SKILL_OF[i]
        other = CATEGORIES[(i + 1) % len(CATEGORIES)]
        out.append(Cand(beatmap_id=i, set_id=i // 2, creator=f"mapper{i % 40}", stars=2.5 + (i % 40) * 0.05,
                        weights={main: 0.7, other: 0.3}, length_s=90 + (i % 5) * 30, playcount=10_000,
                        favourites=100))
    return out


def context(**kw):
    base = dict(ratings={s: 3.0 for s in CATEGORIES}, games={s: 30.0 for s in CATEGORIES}, level=3.5,
                budget_min=45, seed="test", clear_level=3.5, clear_scale=0.55)
    base.update(kw)
    return Context(**base)


def test_plan_fits_the_time_budget_and_has_one_of_each_special_role():
    items, summary = plan(context(), candidates())
    total = sum(i.minutes for i in items)
    assert 0.7 * 45 <= total <= 1.3 * 45
    roles = [i.role for i in items]
    assert roles.count("Échauffement") == 1 and roles[0] == "Échauffement"
    assert roles.count("Défi") <= 1 and roles.count("Révision") <= 1


def test_plan_never_repeats_a_map_or_a_set():
    items, _ = plan(context(), candidates())
    ids = [i.beatmap_id for i in items]
    sets = [i.beatmap_id // 2 for i in items]
    assert len(ids) == len(set(ids)) and len(sets) == len(set(sets))


def test_plan_respects_exclusions_and_no_second_warm_up():
    excluded = set(range(0, 300, 3))
    items, _ = plan(context(exclude=excluded, warmed_up=True, done_roles={"Défi"}), candidates())
    assert not excluded & {i.beatmap_id for i in items}
    roles = [i.role for i in items]
    assert "Échauffement" not in roles and "Défi" not in roles


def test_plan_is_deterministic_for_a_given_day():
    a, _ = plan(context(seed="2026-10-04-0"), candidates())
    b, _ = plan(context(seed="2026-10-04-0"), candidates())
    assert [i.beatmap_id for i in a] == [i.beatmap_id for i in b]


def test_skill_shares_are_capped_and_favour_the_weakest_skill():
    ratings = {s: 3.0 for s in CATEGORIES}
    ratings["accuracy"] = 2.0
    shares, _ = skill_shares(context(ratings=ratings), candidates())
    assert abs(sum(shares.values()) - 1) < 1e-6
    assert max(shares.values()) <= planner.MAX_SHARE + 1e-9
    assert max(shares, key=shares.get) == "accuracy"


def test_unmeasured_skill_gets_a_level_test():
    games = {s: 30.0 for s in CATEGORIES}
    games["tech"] = 0.0
    items, _ = plan(context(games=games), candidates())
    assert any(i.role == "Test de niveau" and i.category == "tech" for i in items)


def test_warm_up_stays_easy_even_if_the_strongest_skill_only_has_hard_short_maps():
    # Stamina est la plus forte, mais ses seules maps courtes sont à 4,3★ : l'échauffement ne doit pas y aller.
    ratings = {s: 3.0 for s in CATEGORIES} | {"stamina": 3.4}
    cands = candidates() + [Cand(beatmap_id=900 + i, set_id=900 + i, creator=f"s{i}", stars=4.3,
                                 weights={"stamina": 1.0}, length_s=120, playcount=10_000, favourites=100)
                            for i in range(5)]
    items, _ = plan(context(ratings=ratings), cands)
    warm = next(i for i in items if i.role == "Échauffement")
    assert warm.beatmap_id < 900 and warm.p >= planner.WARM_MIN_P


def test_challenge_shows_its_chance_to_finish_the_map():
    from osuprog import model
    items, _ = plan(context(), candidates())
    challenge = next(i for i in items if i.role == "Défi")
    c = next(c for c in candidates() if c.beatmap_id == challenge.beatmap_id)
    assert abs(challenge.p - model.finish_prob(c.stars, c.hp, 3.5, 0.55)) < 1e-9


def test_about_a_third_of_the_session_is_played_near_your_limit():
    from osuprog import model
    items, _ = plan(context(), candidates())
    climb = [i for i in items if i.role == "Montée"]
    assert climb and 0.25 * 45 <= sum(i.minutes for i in climb) <= 0.5 * 45
    for i in climb:  # chances de FINIR la map, près de la cible
        c = next(c for c in candidates() if c.beatmap_id == i.beatmap_id)
        assert abs(i.p - model.finish_prob(c.stars, c.hp, 3.5, 0.55)) < 1e-9 and 0.35 <= i.p <= 0.85


def test_a_stricter_objective_is_not_mistaken_for_a_weak_skill():
    # Accuracy a une note basse parce que son objectif est plus exigeant, mais à critère égal elle est au niveau.
    ratings = {s: 3.0 for s in CATEGORIES} | {"accuracy": 2.5}
    shares_raw, _ = skill_shares(context(ratings=ratings), candidates())
    shares_fair, _ = skill_shares(context(ratings=ratings, levels={s: 3.0 for s in CATEGORIES}), candidates())
    assert shares_fair["accuracy"] < shares_raw["accuracy"]
    assert max(shares_fair.values()) - min(shares_fair.values()) < 0.1


def test_climb_chances_are_chances_to_finish_cleanly():
    from osuprog import model
    clean = {"level": 3.2, "scale": 0.5, "hp_k": 0.0}
    items, _ = plan(context(clean=clean), candidates())
    climb = [i for i in items if i.role == "Montée"]
    assert climb
    for i in climb:
        c = next(c for c in candidates() if c.beatmap_id == i.beatmap_id)
        assert abs(i.p - model.finish_prob_for(clean, c.stars, c.hp)) < 1e-9


# --- Zone de confort et maps écartées --------------------------------------------------------

def _cand(**kw) -> Cand:
    base = dict(beatmap_id=1, set_id=1, creator="m", stars=3.5, weights={"aim": 1.0}, length_s=100, hp=4.0,
                ar=7.0, cs=4.0, od=6.0, bpm=170.0)
    base.update(kw)
    return Cand(**base)


COMFORT = {"ar": 8.5, "cs": 4.3, "hp": 5.75}


def test_a_map_inside_your_comfort_zone_is_not_penalised():
    assert planner.comfort_factor(_cand(), COMFORT, []) == 1.0


def test_unusual_settings_lower_a_map_without_banning_it():
    harsh = planner.comfort_factor(_cand(ar=9.2, hp=6.0), COMFORT, [])
    tiny_circles = planner.comfort_factor(_cand(cs=5.0), COMFORT, [])
    assert planner.MIN_COMFORT_FACTOR < harsh < 1.0
    assert planner.MIN_COMFORT_FACTOR < tiny_circles < harsh


def test_maps_like_one_you_removed_come_after_different_ones():
    removed = [{"ar": 9.2, "cs": 4.0, "hp": 6.0, "bpm": 175.0, "skill": "speed"}]
    twin = planner.comfort_factor(_cand(ar=9.0, hp=6.0, bpm=170.0, weights={"speed": 1.0}), {}, removed)
    other = planner.comfort_factor(_cand(ar=5.0, hp=3.0, bpm=120.0, weights={"stamina": 1.0}), {}, removed)
    assert twin < other == 1.0


def test_plan_prefers_comfortable_maps_when_everything_else_is_equal():
    cands = candidates()
    for c in cands:
        c.ar, c.cs = 7.0, 4.0
    harsh_ids = {c.beatmap_id for c in cands if c.beatmap_id % 2 == 0}
    for c in cands:
        if c.beatmap_id in harsh_ids:
            c.ar, c.cs, c.hp = 9.6, 5.5, 7.0
    items, _ = plan(context(comfort=COMFORT), cands)
    chosen = [i.beatmap_id for i in items]
    assert sum(i in harsh_ids for i in chosen) < len(chosen) / 2


def test_joke_maps_with_absurd_settings_are_never_proposed():
    cands = candidates()
    joke = cands[0]
    joke.ar, joke.cs = 0.0, 0.0
    items, _ = plan(context(), cands)
    assert joke.beatmap_id not in [i.beatmap_id for i in items]
    assert planner.is_gimmick(joke) and not planner.is_gimmick(_cand())


def test_tiny_maps_and_very_long_challenges_are_never_proposed():
    """Un extrait de 12 s ne fait rien travailler ; un défi de 5 min, rejoué plusieurs fois, mange la séance."""
    cands = candidates()
    for c in cands[:40]:
        c.length_s = 12
    for c in cands[40:80]:
        c.length_s = 300
    items, _ = plan(context(), cands)
    assert all(i.beatmap_id >= 40 for i in items)
    defi = [i for i in items if i.role == "Défi"]
    assert defi and all(not 40 <= i.beatmap_id < 80 for i in defi)


def test_a_challenge_is_not_a_far_harder_map_with_an_empty_health_bar():
    """HP 1 : une map bien trop dure paraît « finissable » ; le défi reste juste au-dessus de ton niveau."""
    cands = candidates() + [Cand(beatmap_id=950 + i, set_id=950 + i, creator=f"h{i}", stars=4.55 + i * 0.05,
                                 weights={"aim": 1.0}, length_s=120, playcount=10_000, favourites=100, hp=1.0)
                            for i in range(5)]
    items, _ = plan(context(hp_k=0.3), cands)
    defi = next(i for i in items if i.role == "Défi")
    assert defi.beatmap_id < 950


def test_reading_is_trained_on_the_slow_approach_you_will_actually_meet():
    """À 3,5★, tes prochaines maps de reading sont en AR 6-8 : une map en AR 3 n'entraîne presque pas ce reading-là,
    même si elle est plus populaire."""
    ratings = {s: 3.0 for s in CATEGORIES} | {"reading": 2.4}
    cands = candidates() + [Cand(beatmap_id=1000 + i, set_id=1000 + i, creator=f"r{i}", stars=3.0 + (i % 10) * 0.05,
                                 weights={"reading": 0.8, "aim": 0.2}, length_s=120,
                                 playcount=1_000_000 if i % 2 else 10_000, favourites=10_000 if i % 2 else 100,
                                 ar=3.0 if i % 2 else 6.5)
                            for i in range(40)]
    items, _ = plan(context(ratings=ratings), cands)
    reading = [i.beatmap_id for i in items if i.category == "reading"]
    assert reading and all(b % 2 == 0 for b in reading if b >= 1000)
    slow = next(c for c in cands if c.ar == 3.0)
    assert planner.training_weights(slow, 3.5)["reading"] < slow.weights["reading"] / 2
    assert planner.training_weights(cands[-2], 3.5) == cands[-2].weights   # AR 6,5 : rien ne change


def test_a_map_played_on_three_different_days_makes_room_for_a_new_one():
    """Cat Loving : 72, 73, 79, 75, 72 % en 5 séances. Passé 3 jours, une map ne rapporte plus rien."""
    known = planner.History(attempts=6, met=False, days_since=1.0, days_played=3)
    fresh = planner.History(attempts=0)
    assert planner.freshness(known) < planner.freshness(planner.History(attempts=4, met=False, days_since=1.0,
                                                                        days_played=2)) / 3
    assert planner.freshness(known) < planner.freshness(fresh) / 3
    assert planner.freshness(known, tier=True) > planner.freshness(known)   # une map de palier se joue jusqu'au bout
    ctx = context(history={i: planner.History(attempts=6, met=False, days_since=1.0, days_played=3)
                           for i in range(0, 300, 2)})
    items, _ = plan(ctx, candidates())
    # une préférence, pas une interdiction : une map connue ne passe que faute de mieux (au plus 2 sur la séance)
    assert sum(1 for i in items if i.beatmap_id % 2 == 0) <= 2


def test_lagging_skills_get_reserved_training_slots():
    """L'aim est dans presque toutes les maps : sans quota, il accapare le programme et les points faibles attendent."""
    ratings = {s: 3.6 for s in CATEGORIES} | {"reading": 3.0, "stamina": 3.1, "tech": 3.2}
    cands = [Cand(beatmap_id=i, set_id=i, creator=f"m{i}", stars=3.0 + (i % 30) * 0.04, weights=w, length_s=100,
                  playcount=50_000, favourites=500)
             for i in range(240)
             for w in [{CATEGORIES[i % len(CATEGORIES)]: 0.7, "aim": 0.3} if CATEGORIES[i % len(CATEGORIES)] != "aim"
                       else {"aim": 1.0}]]
    items, _ = plan(context(ratings=ratings, levels=ratings, level=3.8, clear_level=3.8, budget_min=30), cands)
    lagging = [i.category for i in items if i.role in ("Entraînement", "Objectif")
               and i.category in ("reading", "stamina", "tech")]
    assert len(lagging) >= planner.WEAK_SLOTS


def test_a_lagging_skill_is_skipped_rather_than_given_an_impossible_map():
    ratings = {s: 3.6 for s in CATEGORIES} | {"stamina": 3.0}
    cands = [Cand(beatmap_id=i, set_id=i, creator=f"m{i}", stars=3.0 + (i % 30) * 0.04, weights={"aim": 1.0},
                  length_s=100, playcount=50_000, favourites=500) for i in range(120)]
    cands.append(Cand(beatmap_id=999, set_id=999, creator="x", stars=6.5, weights={"stamina": 1.0}, length_s=200,
                      playcount=50_000, favourites=500))
    items, _ = plan(context(ratings=ratings, levels=ratings, level=3.8, clear_level=3.8), cands)
    assert 999 not in {i.beatmap_id for i in items}
