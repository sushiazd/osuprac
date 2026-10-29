"""Tests sur une base temporaire : enregistrement des scores, verrou, sauvegardes, contrôle des prévisions…"""
import time
from datetime import date, timedelta

import pytest
from conftest import add_map, score

from osuprog import db, live, planner, progression, rewards
from osuprog.sync import WrongPlayer, _sync, store_score


def test_local_and_online_copies_of_one_play_are_counted_once(conn):
    add_map(conn, 1)
    assert store_score(conn, score(-5, 1, "2026-10-03T20:00:05Z"), "lazer_local")
    assert store_score(conn, score(42, 1, "2026-10-03T20:00:00Z"), "recent")
    ids = [r[0] for r in conn.execute("SELECT score_id FROM scores")]
    assert ids == [42]
    assert not store_score(conn, score(-6, 1, "2026-10-03T20:00:03Z"), "lazer_local")  # déjà là en ligne
    assert store_score(conn, score(-7, 1, "2026-10-03T20:05:00Z"), "lazer_local")      # autre partie


def test_sync_lock_refuses_a_second_sync():
    with db.sync_lock():
        with pytest.raises(db.SyncBusy):
            with db.sync_lock():
                pass
    with db.sync_lock():  # libéré
        pass


def test_daily_backup_keeps_only_the_last_ones(conn, tmp_path):
    folder = tmp_path / "sauvegardes"
    folder.mkdir()
    for i in range(10):
        (folder / f"osu-2026-09-{10 + i:02d}.sqlite").write_bytes(b"x")
    assert db.daily_backup(conn, folder, keep=3)
    assert not db.daily_backup(conn, folder, keep=3)  # une seule par jour
    kept = sorted(p.name for p in folder.glob("*.sqlite"))
    assert len(kept) == 3 and kept[-1] == f"osu-{date.today().isoformat()}.sqlite"


def test_forecast_check_only_judges_the_first_attempt(conn):
    add_map(conn, 7, weights={"aim": 1.0})
    day = "2026-10-03"
    conn.execute("INSERT INTO session_plan (day, pos, role, category, level_c, beatmap_id, p) "
                 "VALUES (?, 0, 'Entraînement', 'aim', 300, 7, 0.4)", (day,))
    store_score(conn, score(1, 7, "2026-10-03T18:00:00Z", accuracy=0.70), "recent")   # 1er essai : raté
    store_score(conn, score(2, 7, "2026-10-03T18:05:00Z", accuracy=0.92), "recent")   # 2e essai : réussi
    check = planner.forecast_check(conn, "2026-10-04")
    assert check["n"] == 1 and check["met"] == 0 and abs(check["predicted"] - 0.4) < 1e-9


def test_ignored_tier_map_leaves_the_tier_and_the_program(conn):
    add_map(conn, 9)
    conn.execute("INSERT INTO tier_maps VALUES ('aim', 300, 0, 9)")
    conn.execute("INSERT INTO session_plan (day, pos, role, category, level_c, beatmap_id) "
                 "VALUES ('2026-10-04', 0, 'Objectif', 'aim', 300, 9)")
    planner.ignore_map(conn, 9, "2026-10-04")
    assert conn.execute("SELECT count(*) FROM tier_maps").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM session_plan").fetchone()[0] == 0
    assert progression.ignored_maps(conn) == {9}
    assert db.get_meta(conn, "retired_tier_maps")[-1]["reason"] == "écartée à ta demande"
    progression.unignore_map(conn, 9)
    assert progression.ignored_maps(conn) == set()


def test_pick_skips_maps_already_failed_many_times(conn):
    db.set_meta(conn, "bundled_pool", "x")
    for bid in (20, 21):
        add_map(conn, bid, stars=3.1, weights={"aim": 1.0})
    for i in range(progression.STUCK_ATTEMPTS):
        store_score(conn, score(100 + i, 20, f"2026-10-0{1 + i % 3}T1{i}:00:00Z", accuracy=0.6), "recent")
    chosen = progression.pick(conn, None, "aim", 300, 325, 2, set(), set(), log=lambda *_: None)
    assert chosen == [21]


def test_streaks_survive_until_the_end_of_the_next_day():
    today = date(2026, 10, 4)
    days = [(today - timedelta(days=i)).isoformat() for i in range(1, 6)]  # hier et les 4 jours d'avant
    st = rewards.streaks(days, today.isoformat())
    assert st == {"current": 5, "best": 5, "played_today": False}
    gap = rewards.streaks([(today - timedelta(days=3)).isoformat()], today.isoformat())
    assert gap["current"] == 0 and gap["best"] == 1


def test_news_announces_tiers_and_nothing_on_first_sync():
    before = {"plays": 10, "tiers": set(), "achievements": {}}
    after = {"plays": 12, "tiers": {("aim", 300), ("exam", 300)}, "achievements": {}}
    kinds = sorted(e["kind"] for e in rewards.news(before, after))
    assert kinds == ["rank", "tier"]
    assert rewards.news({**before, "plays": 0}, after) == []


def test_live_sync_waits_for_a_settled_new_file():
    now = 1_000_000.0
    assert not live.due(None, None, None, now)                          # lazer absent
    assert not live.due(now - 100, now - 100, None, now)                # rien de nouveau
    assert not live.due(now - 2, now - 100, None, now)                  # fichier encore en écriture
    assert live.due(now - 20, now - 100, None, now)
    assert not live.due(now - 20, now - 100, now - 10, now)             # dernière tentative trop récente


class FakeApi:
    def __init__(self, user):
        self._user = user

    def user(self, who):
        return self._user


def test_sync_refuses_to_mix_two_players(monkeypatch, conn, tmp_path):
    db.set_meta(conn, "user", {"id": 1, "username": "moi"})
    conn.commit()
    monkeypatch.setattr(db, "connect", lambda path=None: conn)
    monkeypatch.setattr("osuprog.sync.db.daily_backup", lambda *a, **k: False)
    with pytest.raises(WrongPlayer):
        _sync("un_ami", lambda *_: None, FakeApi({"id": 2, "username": "un_ami"}))


def test_quitting_a_map_is_not_a_fail(conn):
    """Échap après quelques notes : osu! l'enregistre comme un fail, l'outil l'ignore (notes, séance, prévisions)."""
    from osuprog import rating
    add_map(conn, 5, n_objects=300)
    quit_ = score(1, 5, "2026-10-08T20:00:00Z", accuracy=0.8, miss=1, passed=False)
    quit_["statistics"] = {"great": 10, "ok": 2, "meh": 0, "miss": 1}
    died = score(2, 5, "2026-10-08T20:05:00Z", accuracy=0.6, miss=25, passed=False)
    died["statistics"] = {"great": 100, "ok": 40, "meh": 10, "miss": 25}
    store_score(conn, quit_, "recent")
    store_score(conn, died, "recent")
    flags = dict(conn.execute("SELECT score_id, abandoned FROM scores").fetchall())
    assert flags == {1: 1, 2: 0}
    assert [p["n_miss"] for p in progression.played_on(conn, 5, "2026-10-08")] == [25]
    rating.recompute(conn)
    assert {r[0] for r in conn.execute("SELECT DISTINCT score_id FROM rating_events")} == {2}


def test_a_climb_map_counts_only_if_finished_cleanly(conn):
    """Montée : finie avec au plus 8 % de miss. Survivre avec 50 miss (jauge de vie basse) reste à retenter."""
    from osuprog.ui import item_state
    add_map(conn, 6, n_objects=300)  # 8 % de 300 = 24 miss au plus
    day = "2026-10-11"
    assert item_state(conn, "Montée", "aim", 6, day)[0] == "todo"
    store_score(conn, score(1, 6, "2026-10-11T13:00:00Z", accuracy=0.57, miss=50), "recent")
    assert item_state(conn, "Montée", "aim", 6, day)[0] == "retry"
    store_score(conn, score(2, 6, "2026-10-11T13:10:00Z", accuracy=0.70, miss=20), "recent")
    assert item_state(conn, "Montée", "aim", 6, day)[0] == "done"  # précision libre, peu de miss


def test_clean_finish_level_is_below_the_finish_level(conn):
    """Finir proprement est plus dur que survivre : son niveau est plus bas."""
    from osuprog import rating
    sid = 0
    for i in range(60):
        stars = 2.5 + (i % 20) * 0.1
        add_map(conn, 100 + i, stars=stars, n_objects=300)
        sid += 1
        passed = stars < 4.0
        misses = 5 if stars < 3.3 else 40  # au-dessus de 3,3★ : finies, mais avec beaucoup de miss
        store_score(conn, score(sid, 100 + i, f"2026-10-0{1 + i % 9}T12:{i:02d}:00Z", miss=misses if passed else 30,
                                passed=passed), "recent")
    clear, clean = rating.clear_level(conn), rating.clear_level(conn, clean=True)
    assert clean["level"] < clear["level"] - 0.4
