"""Maps similaires : proximité, exclusions, repli sans fichiers, estimation des pp d'un bon run."""
import json

import pytest

from conftest import add_map
from osuprog import db, osufile, similar

PP_FC = [40.0, 45.0, 50.0, 60.0, 80.0]
BASE = {"aim": 2.2, "speed": 1.6, "ar": 8.8, "cs": 3.6, "od": 7.0, "stream_frac": 0.0, "slider_factor": 0.99,
        "max_combo": 380, "n_objects": 230, "length_s": 72.0, "hp": 4.0, "pp_fc": PP_FC}
FLAT_MOVEMENT = {"jump_vel": 14.0, "sharp_frac": 0.5, "rhythm_irregular": 0.2, "sv_changes": 2.0}


def add(conn, beatmap_id, stars=4.0, set_id=None, status="ranked", **changes):
    add_map(conn, beatmap_id, stars=stars, set_id=set_id, status=status)
    conn.execute("UPDATE map_attrs SET attrs = ? WHERE beatmap_id = ?", (json.dumps({**BASE, **changes}), beatmap_id))


@pytest.fixture(autouse=True)
def no_files(monkeypatch):
    """Par défaut, aucun fichier .osu : la recherche garde l'ordre du catalogue et ne touche pas au réseau."""
    monkeypatch.setattr(osufile, "download_many", lambda ids, progress=None: set(ids))


def ids(found):
    return [m["beatmap_id"] for m in found["maps"]]


def test_closest_map_comes_first(conn):
    add(conn, 1)
    add(conn, 2, aim=2.3)
    add(conn, 3, aim=2.6, speed=1.3)
    assert ids(similar.find(conn, 1)) == [2, 3]


def test_unknown_or_unanalysed_map_gives_none(conn):
    assert similar.find(conn, 999) is None
    add_map(conn, 5)  # sans aim, speed, etc.
    assert similar.find(conn, 5) is None


def test_excludes_same_mapset_played_and_ignored_maps(conn):
    add(conn, 1, set_id=100)
    add(conn, 2, set_id=100)
    add(conn, 3)
    add(conn, 4)
    add(conn, 6)
    conn.execute("INSERT INTO scores (score_id, beatmap_id, mods, passed, countable) VALUES (1, 3, '[]', 1, 1)")
    db.set_meta(conn, "ignored_maps", [{"id": 4, "at": "2026-11-01T00:00:00Z"}])
    assert ids(similar.find(conn, 1)) == [6]


def test_keeps_one_difficulty_per_mapset(conn):
    add(conn, 1)
    add(conn, 2, set_id=200)
    add(conn, 3, set_id=200, aim=2.4)
    assert ids(similar.find(conn, 1)) == [2]


def test_rejects_other_formats_status_and_star_range(conn):
    add(conn, 1)
    add(conn, 2, stream_frac=0.3)
    add(conn, 3, length_s=300.0)
    add(conn, 4, hp=6.0)
    add(conn, 6, status="loved")
    add(conn, 7, stars=4.8)
    add(conn, 8)
    assert ids(similar.find(conn, 1)) == [8]


def test_jumps_can_change_the_order(conn, monkeypatch):
    add(conn, 1)
    add(conn, 2, aim=2.25)  # plus proche sur le catalogue, sauts très différents
    add(conn, 3, aim=2.3)
    add(conn, 4, aim=3.2, speed=1.2)  # élargit l'écart type : sans elle, 0,05 d'aim pèserait autant qu'un écart énorme
    assert ids(similar.find(conn, 1))[:2] == [2, 3]
    moves = {1: FLAT_MOVEMENT, 2: {**FLAT_MOVEMENT, "jump_vel": 30.0}, 3: FLAT_MOVEMENT, 4: FLAT_MOVEMENT}
    monkeypatch.setattr(osufile, "download_many", lambda ids, progress=None: set())
    monkeypatch.setattr(similar, "_movement", moves.get)
    assert ids(similar.find(conn, 1))[:2] == [3, 2]


def test_missing_candidate_file_is_dropped_but_others_stay(conn, monkeypatch):
    add(conn, 1)
    add(conn, 2)
    add(conn, 3)
    monkeypatch.setattr(osufile, "download_many", lambda ids, progress=None: {2})
    monkeypatch.setattr(similar, "_movement", lambda i: FLAT_MOVEMENT)
    assert ids(similar.find(conn, 1)) == [3]


def test_good_run_needs_enough_plays_and_scales_pp(conn):
    add(conn, 1)
    add(conn, 2)
    found = similar.find(conn, 1)
    assert not found["estimated"]
    for i in range(similar.MIN_PLAYS):
        conn.execute(
            "INSERT INTO scores (score_id, beatmap_id, mods, accuracy, passed, pp, ended_at, countable)"
            " VALUES (?, 1, '[]', 90.0, 1, 25.0, '2026-01-01T00:00:00Z', 1)", (i + 1,))
    assert similar.good_run(conn, 4.0) == (90.0, pytest.approx(0.5))
    assert similar.good_run(conn, 6.0) is None
    found = similar.find(conn, 1)
    assert found["estimated"]
    assert found["maps"][0]["pp"] == pytest.approx(25.0)  # 50 pp en full combo à 90 %, rendement de 0,5
    assert found["ref"]["best_pp"] == 25.0
