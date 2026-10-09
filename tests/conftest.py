"""Les tests n'écrivent jamais dans ta vraie base : OSU_DATA_DIR pointe vers un dossier temporaire
AVANT le premier import d'osuprog (config lit la variable à l'import)."""
import json
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="osuprog-tests-")
os.environ["OSU_DATA_DIR"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from osuprog import db  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.sqlite")
    yield c
    c.close()


def add_map(conn, beatmap_id, stars=3.0, weights=None, set_id=None, n_objects=300, hp=4.0, status="ranked",
            length_s=120.0, mods_key="", playcount=100_000, pp_fc=None):
    weights = weights or {"aim": 1.0}
    conn.execute("INSERT OR REPLACE INTO beatmaps VALUES (?, ?, 'Artiste', ?, 'Hard', ?, ?, ?, 10)",
                 (beatmap_id, set_id or beatmap_id, f"Map {beatmap_id}", f"Mapper {beatmap_id}", status, playcount))
    attrs = {"stars": stars, "hp": hp, "n_objects": n_objects, "length_s": length_s}
    if pp_fc:
        attrs["pp_fc"] = pp_fc
    conn.execute("INSERT OR REPLACE INTO map_attrs VALUES (?, ?, ?, ?, ?, ?)",
                 (beatmap_id, mods_key, stars, max(weights, key=weights.get), json.dumps(attrs), json.dumps(weights)))


def score(score_id, beatmap_id, ended_at, accuracy=0.9, miss=0, passed=True, mods=None):
    """Score au format de l'API osu! (accuracy entre 0 et 1)."""
    return {"id": score_id, "beatmap_id": beatmap_id, "ended_at": ended_at, "accuracy": accuracy,
            "max_combo": 100, "statistics": {"great": 200, "ok": 10, "meh": 0, "miss": miss},
            "passed": passed, "rank": "A" if passed else "F", "pp": 10.0, "mods": mods or []}
