"""La séance du jour reste stable : jouer, écarter ou rallonger ne retire pas tout au sort."""
import time
from datetime import date, datetime, timezone

from conftest import add_map, score

from osuprog import live, planner
from osuprog.skills import CATEGORIES
from osuprog.sync import store_score

DAY = date.today().isoformat()


def setup_pool(conn, n=120):
    for i in range(1, n + 1):
        cat = CATEGORIES[i % len(CATEGORIES)]
        add_map(conn, i, stars=2.6 + (i % 30) * 0.05, weights={cat: 0.7, "aim" if cat != "aim" else "speed": 0.3},
                length_s=90 + (i % 4) * 30)
    conn.executemany("INSERT INTO ratings VALUES (?, 3.0, 30)", [(c,) for c in CATEGORIES])
    conn.commit()


def plan_ids(conn):
    return [r["beatmap_id"] for r in conn.execute("SELECT beatmap_id FROM session_plan WHERE day = ? ORDER BY pos", (DAY,))]


def ratings(conn):
    return {r["category"]: {"rating": r["rating"], "games": r["games"]} for r in conn.execute("SELECT * FROM ratings")}


def play(conn, beatmap_id, score_id):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    store_score(conn, score(score_id, beatmap_id, now, accuracy=0.7), "recent")
    conn.commit()


def test_playing_a_map_keeps_the_rest_of_the_list(conn):
    setup_pool(conn)
    planner.build_session(conn, None, ratings(conn), DAY, log=lambda *_: None)
    before = plan_ids(conn)
    assert len(before) >= 4
    play(conn, before[0], 1)
    planner.build_session(conn, None, ratings(conn), DAY, log=lambda *_: None)
    after = plan_ids(conn)
    assert after[:len(before) - 1] == before[:len(before) - 1] or after[1:len(before)] == before[1:]
    assert after[0] == before[0]


def test_ignoring_a_map_only_replaces_that_map(conn):
    setup_pool(conn)
    planner.build_session(conn, None, ratings(conn), DAY, log=lambda *_: None)
    before = plan_ids(conn)
    victim = before[2]
    planner.ignore_map(conn, victim, DAY)
    planner.build_session(conn, None, ratings(conn), DAY, log=lambda *_: None)
    after = plan_ids(conn)
    assert victim not in after
    assert after[:len(before) - 1] == [b for b in before if b != victim]  # même ordre, une map en moins…
    assert len(after) >= len(before) - 1                                  # …remplacée en fin de liste si le temps le permet


def test_ignoring_a_played_map_keeps_it_in_the_finished_list(conn):
    setup_pool(conn)
    planner.build_session(conn, None, ratings(conn), DAY, log=lambda *_: None)
    first = plan_ids(conn)[0]
    play(conn, first, 1)
    planner.ignore_map(conn, first, DAY)
    planner.build_session(conn, None, ratings(conn), DAY, log=lambda *_: None)
    assert plan_ids(conn)[0] == first


def test_asking_for_other_maps_does_redraw(conn):
    setup_pool(conn)
    planner.build_session(conn, None, ratings(conn), DAY, log=lambda *_: None)
    before = plan_ids(conn)
    planner.reset_session(conn, DAY)
    planner.build_session(conn, None, ratings(conn), DAY, log=lambda *_: None)
    assert plan_ids(conn) != before


def test_fit_pending_trims_from_the_end_only_when_over_budget():
    assert planner.fit_pending([3, 3, 3], used=20, budget=30) == 3
    assert planner.fit_pending([3, 3, 3], used=26, budget=30) == 2
    assert planner.fit_pending([3, 3, 3], used=40, budget=30) == 0


def test_new_replay_folder_is_detected(tmp_path):
    files = tmp_path / "files"
    (files / "a" / "ab").mkdir(parents=True)
    (files / "c" / "cd").mkdir(parents=True)
    before = live.newest_subfolder_mtime(files)
    time.sleep(1.2)
    (files / "c" / "cd" / "replay").write_bytes(b"x")  # lazer dépose un replay
    assert live.newest_subfolder_mtime(files) >= before + 1
    assert live.newest_subfolder_mtime(tmp_path / "absent") == 0.0
