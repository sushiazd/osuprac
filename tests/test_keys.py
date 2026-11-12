import os

from osuprog import keys
from osuprog.keys import parse_keys, save_env

SECRET = "AbCdEf0123456789AbCdEf0123456789AbCdEf01"


def test_parse_values_alone_any_order():
    assert parse_keys(f"12345\n{SECRET}") == ("12345", SECRET)
    assert parse_keys(f"{SECRET} 12345") == ("12345", SECRET)


def test_parse_osu_page_block():
    pasted = (f"Mon outil\nID client\n48213\nSecret client\n{SECRET}\nURL de rappel\nhttp://localhost:8080\n"
              "Éditer Supprimer")
    assert parse_keys(pasted) == ("48213", SECRET)


def test_parse_ignores_digits_inside_secret_and_urls():
    assert parse_keys(f"https://osu.ppy.sh/home/account/edit/99 {SECRET}") == (None, SECRET)
    assert parse_keys("12345") == ("12345", None)
    assert parse_keys("") == (None, None)


def test_save_env_keeps_other_lines(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# commentaire\nOSU_CLIENT_ID=\nOSU_DATA_DIR=C:\\data\n", encoding="utf-8")
    monkeypatch.setattr(os, "environ", dict(os.environ))
    save_env({"OSU_CLIENT_ID": "1", "OSU_USERNAME": "joueur"}, path=env)
    assert env.read_text(encoding="utf-8") == ("# commentaire\nOSU_CLIENT_ID=1\nOSU_DATA_DIR=C:\\data\n"
                                               "OSU_USERNAME=joueur\n")
    assert os.environ["OSU_USERNAME"] == "joueur"


def test_save_env_starts_from_example(tmp_path, monkeypatch):
    example = tmp_path / ".env.example"
    example.write_text("# aide\nOSU_CLIENT_ID=\nOSU_CLIENT_SECRET=\n", encoding="utf-8")
    monkeypatch.setattr(keys, "EXAMPLE_PATH", example)
    monkeypatch.setattr(os, "environ", dict(os.environ))
    env = tmp_path / ".env"
    save_env({"OSU_CLIENT_ID": "7", "OSU_CLIENT_SECRET": SECRET}, path=env)
    assert env.read_text(encoding="utf-8") == f"# aide\nOSU_CLIENT_ID=7\nOSU_CLIENT_SECRET={SECRET}\n"
