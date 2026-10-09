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
