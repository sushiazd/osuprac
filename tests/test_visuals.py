from pathlib import Path

from osuprog import visuals

THEME = Path(__file__).resolve().parent.parent / "osuprog" / "theme.css"
INFO = {"title": "<img src=x onerror=alert(1)>", "artist": "A & B", "version": "Hard", "stars": 3.2,
        "set_id": 1, "attrs": {"length_s": 125}}


def test_theme_css_has_no_angle_bracket():
    # Le filtre HTML de Streamlit (DOMPurify) supprime toute la feuille de style s'il y voit un « < ».
    assert "<" not in THEME.read_text(encoding="utf-8")


def test_default_osu_avatar_becomes_an_initial():
    guest = {"username": "azdsushi", "avatar_url": "https://osu.ppy.sh/images/layout/avatar-guest@2x.png"}
    assert not visuals.has_photo(guest)
    assert "<img" not in visuals.avatar(guest) and ">A<" in visuals.avatar(guest)
    photo = {"username": "x", "avatar_url": "https://a.ppy.sh/1?123.png"}
    assert visuals.has_photo(photo) and "<img" in visuals.avatar(photo)


def test_radar_is_an_image_not_inline_svg():
    # Streamlit supprime le SVG intégré : l'hexagone doit passer par une image en data URI.
    skills = ["aim", "speed", "stamina", "accuracy", "reading", "tech"]
    out = visuals.radar(skills, {s: s.title() for s in skills}, {s: 3.0 for s in skills})
    assert "<svg" not in out and "data:image/svg+xml;base64," in out


def test_map_texts_are_escaped():
    for out in (visuals.map_row(INFO, number=1, role="Objectif", p=0.5, reason='"><b>x'),
                visuals.now_card(INFO, role="Objectif", skill_label="Aim", goal="80 %", p=0.5, minutes=3)):
        assert "<img src=x" not in out and "&lt;img" in out and "A &amp; B" in out
    assert "<b>x" not in visuals.map_row(INFO, number=1, role="Objectif", reason='"><b>x')


def test_tier_tracks_share_one_scale():
    out = visuals.tier_tracks([{"label": "Aim", "current_c": 300, "step_c": 25, "met": 1, "needed": 3},
                               {"label": "Acc", "current_c": 275, "step_c": 25, "met": 0, "needed": 3}])
    aim, acc = out.split('class="op-track-name"')[1:]
    assert aim.count("op-step-current") == 1 and acc.count("op-step-current") == 1
    assert aim.count("op-step-done") == acc.count("op-step-done") + 1  # Aim a un palier d'avance
