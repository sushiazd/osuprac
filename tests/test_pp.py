"""pp : pondération, bonus, interpolation du catalogue, chasse aux pp, réparation de la base."""
import json
import sqlite3

import pytest
from conftest import add_map, score

from osuprog import db, planner, pp, ppcalc
from osuprog.sync import store_score


def test_bonus_and_weighting_follow_osu():
    # Ton profil huismetbenen : 124 maps classées -> 192,871 pp de bonus.
    assert ppcalc.bonus_pp(124) == pytest.approx(192.871, abs=0.01)
    assert ppcalc.bonus_pp(5000) == pytest.approx(ppcalc.bonus_pp(1000))
    assert ppcalc.weighted([10, 10]) == pytest.approx(19.5)


def test_fc_pp_interpolates_between_catalogue_accuracies():
    attrs = {"pp_fc": [10, 20, 30, 40, 60]}  # 80, 85, 90, 95, 100 %
    assert pp.fc_pp(attrs, 0.90) == pytest.approx(30)
    assert pp.fc_pp(attrs, 0.925) == pytest.approx(35)
    assert pp.fc_pp(attrs, 0.50) == pytest.approx(10)  # bornée
    assert pp.fc_pp({}, 0.9) is None


def test_beatmap_links_are_understood():
    assert pp.parse_beatmap_id("545933") == 545933
    assert pp.parse_beatmap_id("https://osu.ppy.sh/beatmaps/545933") == 545933
    assert pp.parse_beatmap_id("https://osu.ppy.sh/beatmapsets/123#osu/545933") == 545933
    assert pp.parse_beatmap_id("osu://b/545933") == 545933
    assert pp.parse_beatmap_id("Rammstein") is None


def test_full_statistics_are_kept_and_completed_later(conn):
    add_map(conn, 1)
    s = score(42, 1, "2026-10-03T20:00:00Z")
    s["statistics"] = {"great": 200, "ok": 10, "meh": 0, "miss": 0}
    store_score(conn, s, "recent")
    s["statistics"] = {**s["statistics"], "slider_tail_hit": 33}
    store_score(conn, s, "best")  # même partie revenue avec les fins de sliders
    stats = json.loads(conn.execute("SELECT stats FROM scores WHERE score_id = 42").fetchone()[0])
    assert stats["slider_tail_hit"] == 33


def _scored(conn, score_id, beatmap_id, pp_value, aim):
    store_score(conn, score(score_id, beatmap_id, "2026-10-03T20:00:00Z"), "best")
    conn.execute("UPDATE scores SET pp = ? WHERE score_id = ?", (pp_value, score_id))
    conn.execute("INSERT INTO score_pp VALUES (?, 'test', ?, ?, 1, 1, 0, 0, 1)", (score_id, pp_value, aim))


def test_profile_keeps_the_best_play_per_map_and_weights_skills(conn):
    add_map(conn, 1)
    add_map(conn, 2)
    _scored(conn, 10, 1, 30.0, 25.0)
    _scored(conn, 11, 1, 20.0, 15.0)  # moins bonne partie sur la même map : ignorée
    _scored(conn, 12, 2, 10.0, 8.0)
    store_score(conn, score(13, 2, "2026-10-03T21:00:00Z"), "recent")
    conn.execute("UPDATE scores SET pp = NULL WHERE score_id = 13")  # relax… : osu! ne donne pas de pp
    conn.execute("INSERT INTO score_pp VALUES (13, 'test', 99, 99, 1, 1, 0, 0, 1)")
    prof = pp.profile(conn)
    assert [s["score_id"] for s in prof["scores"]] == [10, 12]
    assert prof["weighted"] == pytest.approx(30 + 10 * 0.95)
    assert prof["skills"]["aim"] == pytest.approx(25 + 8 * 0.95)
    assert prof["skills"]["speed"] == pytest.approx(1 + 0.95)  # chaque compétence est triée à part
    assert prof["bonus"] == pytest.approx(ppcalc.bonus_pp(2))
    db.set_meta(conn, "user", {"pp": 100.0})
    assert pp.profile(conn)["total"] == 100.0  # le total affiché est celui de ton profil osu!
    assert pp.gain_if(prof, 40.0) > pp.gain_if(prof, 15.0) > 0


def test_hunt_finds_maps_worth_the_target_and_skips_the_rest(conn):
    curve = [30, 40, 50, 60, 80]  # 50 pp en FC à 90 %
    add_map(conn, 1, stars=3.9, n_objects=120, pp_fc=curve)                 # courte : la plus accessible
    add_map(conn, 2, stars=3.9, n_objects=600, pp_fc=curve)                 # longue : plus de miss attendus
    add_map(conn, 3, stars=3.0, pp_fc=[10, 12, 14, 16, 20])                 # trop peu de pp
    add_map(conn, 4, stars=5.5, pp_fc=[100, 120, 140, 160, 200])            # bien trop au-dessus
    add_map(conn, 5, stars=3.9, pp_fc=curve, status="loved")                # pas de pp en loved
    add_map(conn, 6, stars=3.9, pp_fc=curve, playcount=10)                  # quasi jamais jouée
    found = [m["beatmap_id"] for m in pp.hunt(conn, 50, 0.90)]
    assert found == [1, 2]


def test_planner_only_loads_maps_near_your_level(conn):
    add_map(conn, 1, stars=3.0)
    add_map(conn, 2, stars=7.5)
    add_map(conn, 3, stars=3.0, playcount=10)
    window = planner.candidate_window({"aim": {"rating": 3.0}, "speed": {"rating": 3.2}})
    assert [c.beatmap_id for c in planner.load_candidates(conn, window)] == [1]


def test_corrupt_database_is_restored_from_the_last_good_backup(tmp_path):
    path = tmp_path / "osu.sqlite"
    conn = db.connect(path)
    add_map(conn, 1)
    conn.commit()
    folder = tmp_path / "sauvegardes"
    assert db.daily_backup(conn, folder)
    add_map(conn, 2)
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    data = bytearray(path.read_bytes())
    for page in range(1, len(data) // 4096):  # abîme toutes les pages sauf l'en-tête
        data[page * 4096 + 8:page * 4096 + 40] = b"\xff" * 32
    path.write_bytes(bytes(data))
    assert db.repair_if_corrupt(path, folder, log=lambda *_: None)
    conn = sqlite3.connect(path)
    assert db.is_healthy(conn)
    assert [r[0] for r in conn.execute("SELECT beatmap_id FROM beatmaps")] == [1]
    conn.close()
    assert not db.repair_if_corrupt(path, folder, log=lambda *_: None)  # saine : rien à faire
    assert len(list(tmp_path.glob("abimee-*"))) == 1                    # copie de l'ancienne gardée
