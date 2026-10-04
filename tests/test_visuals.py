from pathlib import Path

from osuprog import visuals

THEME = Path(__file__).resolve().parent.parent / "osuprog" / "theme.css"


def test_theme_css_has_no_angle_bracket():
    # Le filtre HTML de Streamlit (DOMPurify) supprime toute la feuille de style s'il y voit un « < ».
    assert "<" not in THEME.read_text(encoding="utf-8")


def test_star_colors_follow_the_lazer_spectrum():
    assert visuals.star_color(2.0) == "#4fffd5"
    assert visuals.star_color(0.0) == "#4290fb"
    assert visuals.star_color(12) == "#000000"
    assert visuals.star_color(2.25) not in ("#4fffd5", "#7cff4f")  # interpolé entre deux points


def test_html_never_embeds_raw_svg():
    # Streamlit supprime le SVG intégré : l'hexagone doit passer par une image en data URI.
    skills = ["aim", "speed", "stamina", "accuracy", "reading", "tech"]
    labels = {s: s.title() for s in skills}
    out = visuals.radar(skills, labels, {s: 3.0 for s in skills}, {s: 0.2 for s in skills}, clear_level=3.7)
    assert "<svg" not in out and "data:image/svg+xml;base64," in out
    assert "<svg" not in visuals.ring(0.4)


def test_map_titles_are_escaped():
    info = {"title": "<img src=x onerror=alert(1)>", "artist": "A & B", "version": "Hard", "stars": 3.2,
            "set_id": 1, "attrs": {}}
    out = visuals.map_card(info, number=1, role="Objectif", cat="aim", skill_label="Aim", state=None,
                           reason="", goal="", p=0.5, minutes=3)
    assert "<img src=x" not in out and "&lt;img" in out and "A &amp; B" in out


def test_tier_track_fill_points_at_the_current_tier_even_near_zero():
    assert "--fill:40%" in visuals.tier_track("aim", "Aim", 300, 25, 1, 3)   # 2 validés, en cours, 3 à venir
    assert "--fill:0%" in visuals.tier_track("aim", "Aim", 25, 25, 0, 3)     # premier palier
