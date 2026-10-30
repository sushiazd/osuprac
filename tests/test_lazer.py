"""Import des parties locales de lazer, sans Node.js ni vrai client.realm : l'export est simulé."""
import pytest

from osuprog import lazer
from osuprog.sync import store_score


def lazer_score(online_id=0, uuid="12345678-1234-5678-1234-567812345678", beatmap_id=10, user_id=1,
                ended_at="2026-10-03T20:00:00Z", **extra):
    """Score au format de tools/lazer-export/export.js."""
    return {"online_id": online_id, "uuid": uuid, "beatmap_id": beatmap_id, "beatmapset_id": beatmap_id,
            "user_id": user_id, "version": "Hard", "status": "ranked", "artist": "Artiste", "title": "Titre",
            "creator": "Mapper", "mods": [], "accuracy": 0.9, "max_combo": 100,
            "statistics": {"great": 200, "ok": 10, "meh": 0, "miss": 1}, "passed": True, "rank": "A", "pp": 10.0,
            "ended_at": ended_at, "beatmap_hash": "", "replay_hash": "", **extra}


@pytest.fixture
def fake_lazer(monkeypatch, tmp_path):
    """Lazer « installé » dans tmp_path, avec la liste de scores que le test remplit."""
    scores = []
    (tmp_path / "files").mkdir()
    monkeypatch.setattr(lazer, "lazer_dir", lambda: tmp_path)
    monkeypatch.setattr(lazer, "available", lambda: None)
    monkeypatch.setattr(lazer, "export_scores", lambda: scores)
    monkeypatch.setattr(lazer, "osu_path", lambda bid: tmp_path / f"{bid}.osu")
    return scores


def test_local_score_id_is_online_id_or_a_stable_negative():
    assert lazer.local_score_id(lazer_score(online_id=42)) == 42
    a = lazer.local_score_id(lazer_score())
    assert a < 0 and a == lazer.local_score_id(lazer_score())
    assert a != lazer.local_score_id(lazer_score(uuid="87654321-4321-8765-4321-876543218765"))


def test_lazer_dir_follows_storage_ini(monkeypatch, tmp_path):
    appdata, custom = tmp_path / "appdata", tmp_path / "ailleurs"
    (appdata / "osu").mkdir(parents=True)
    custom.mkdir()
    monkeypatch.setenv("APPDATA", str(appdata))
    assert lazer.lazer_dir() is None
    (appdata / "osu" / "client.realm").touch()
    assert lazer.lazer_dir() == appdata / "osu"
    (appdata / "osu" / "storage.ini").write_text(f"FullPath = {custom}\n", encoding="utf-8")
    assert lazer.lazer_dir() == appdata / "osu"  # dossier choisi sans client.realm : on garde le dossier par défaut
    (custom / "client.realm").touch()
    assert lazer.lazer_dir() == custom


def test_import_keeps_own_and_guest_plays_only(conn, fake_lazer):
    fake_lazer += [lazer_score(user_id=1, uuid="00000000-0000-0000-0000-000000000001"),
                   lazer_score(user_id=0, uuid="00000000-0000-0000-0000-000000000002", ended_at="2026-10-03T21:00:00Z"),
                   lazer_score(user_id=99, uuid="00000000-0000-0000-0000-000000000003"),
                   lazer_score(beatmap_id=0, uuid="00000000-0000-0000-0000-000000000004")]
    assert lazer.import_local(conn, 1, store_score, log=lambda *_: None) == 2
    assert conn.execute("SELECT count(*) FROM scores").fetchone()[0] == 2
    assert conn.execute("SELECT title FROM beatmaps WHERE beatmap_id = 10").fetchone()[0] == "Titre"
    assert lazer.import_local(conn, 1, store_score, log=lambda *_: None) == 0  # deuxième passage : rien de neuf


def test_import_copies_the_osu_file_from_lazer_storage(conn, fake_lazer, tmp_path):
    h = "abcdef0123"
    (tmp_path / "files" / "a" / "ab").mkdir(parents=True)
    (tmp_path / "files" / "a" / "ab" / h).write_text("osu file format v14")
    fake_lazer.append(lazer_score(beatmap_hash=h))
    lazer.import_local(conn, 1, store_score, log=lambda *_: None)
    assert (tmp_path / "10.osu").read_text() == "osu file format v14"


def test_import_is_skipped_when_lazer_is_unavailable(conn, monkeypatch):
    monkeypatch.setattr(lazer, "available", lambda: "osu! lazer introuvable sur ce PC")
    logs = []
    assert lazer.import_local(conn, 1, store_score, log=logs.append) == 0
    assert "introuvable" in logs[0]


def test_stored_score_id_finds_the_online_copy(conn, fake_lazer):
    s = lazer_score()
    assert lazer.stored_score_id(conn, s) is None
    store_score(conn, {"id": 777, "beatmap_id": 10, "mods": [], "accuracy": 0.9, "statistics": {},
                       "passed": True, "ended_at": "2026-10-03T20:00:05Z"}, "recent")
    assert lazer.stored_score_id(conn, s) == 777
